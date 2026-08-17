"""
core/monitor_worker.py — バックグラウンド常駐監視ワーカースレッド

UIスレッドから独立してバックグラウンドで並列Ping監視を実行し、
結果をSQLiteに書き込み、アラート通知を行う。
これにより、ユーザーが画面操作（フォーム入力等）を行っている最中に
UIがブロッキングされたり、入力値がリセットされるのを防止する。
"""
import threading
import time
import logging
from datetime import datetime
from typing import Optional

from .database import Database
from .ping import PingChecker
from .notifier import Notifier

logger = logging.getLogger(__name__)


class MonitorWorker:
    _instance: Optional["MonitorWorker"] = None
    _lock = threading.Lock()

    def __init__(self, db: Database, checker: PingChecker, notifier: Notifier, config: dict):
        self.db = db
        self.checker = checker
        self.notifier = notifier
        self.config = config

        self._running = False
        self._interval = config.get("polling", {}).get("default_interval_seconds", 60)
        self._thread: Optional[threading.Thread] = None
        self._last_run: Optional[str] = None
        self._last_error: Optional[str] = None
        self._is_checking = False
        self._trigger_event = threading.Event()

    @classmethod
    def get_instance(cls, db: Database, checker: PingChecker, notifier: Notifier, config: dict) -> "MonitorWorker":
        """シングルトンインスタンスを取得する。"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db, checker, notifier, config)
            else:
                cls._instance.config = config
                cls._instance.db = db
                cls._instance.checker = checker
                cls._instance.notifier = notifier
            return cls._instance

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def is_checking(self) -> bool:
        return self._is_checking

    @property
    def last_run(self) -> Optional[str]:
        return self._last_run

    @property
    def interval(self) -> int:
        return self._interval

    def set_interval(self, seconds: int):
        self._interval = max(5, int(seconds))

    def start(self):
        """バックグラウンド監視スレッドを開始する。"""
        with self._lock:
            if self.is_running:
                return
            self._running = True
            self._trigger_event.clear()
            self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="NetOpsMonitorWorker")
            self._thread.start()
            logger.info("MonitorWorker started with interval %ds", self._interval)

    def stop(self):
        """バックグラウンド監視スレッドを停止する。"""
        with self._lock:
            self._running = False
            self._trigger_event.set()
            logger.info("MonitorWorker stopped")

    def trigger_now(self):
        """即時チェックを要求する（スリープを解除して即実行）。"""
        self._trigger_event.set()

    def run_check_sync(self) -> list:
        """同期的に1回チェックを実行する。"""
        return self._execute_check()

    def _worker_loop(self):
        while self._running:
            self._execute_check()
            # 指定秒数待機（trigger_now で即座に起きる）
            self._trigger_event.wait(timeout=self._interval)
            self._trigger_event.clear()

    def _execute_check(self) -> list:
        self._is_checking = True
        try:
            devices = self.db.get_all_devices()
            if devices.empty:
                self._last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return []

            device_dicts = devices.to_dict("records")
            results = self.checker.ping_all(device_dicts)

            cooldown = self.config.get("notifications", {}).get("cooldown_seconds", 300)
            for r in results:
                if r.device_id:
                    # DB に記録
                    self.db.record_ping(r.device_id, r.status, r.latency_ms, r.error_type, r.method)

                    # アラート判定
                    if self.db.should_alert(r.device_id, r.status, cooldown):
                        prev = self.db.get_alert_state(r.device_id)
                        if r.status == "Offline":
                            self.notifier.notify_offline(r.host, r.ip, r.error_type)
                        elif r.status == "Online" and prev.get("last_status") == "Offline":
                            self.notifier.notify_recovery(r.host, r.ip, r.latency_ms)
                        self.db.update_alert_state(r.device_id, r.status)

            self._last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return results
        except Exception as e:
            logger.error("Error during background check: %s", e)
            self._last_error = str(e)
            return []
        finally:
            self._is_checking = False
