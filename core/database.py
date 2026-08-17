"""
core/database.py — SQLite永続化モジュール

テーブル:
  devices       : 監視対象機器一覧（設定情報）
  ping_history  : Ping結果の履歴ログ
  alert_state   : 通知クールダウン管理

主な機能:
  - 機器CRUD
  - Ping結果の記録
  - 稼働率（Uptime）計算（24h / 7d）
  - 機器グループ管理
  - 履歴CSVエクスポート
"""
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd


class Database:
    def __init__(self, db_path: str = "netops.db"):
        self.db_path = db_path
        self._init_db()

    # ─────────────────────────────────────────────
    # 初期化
    # ─────────────────────────────────────────────

    def _init_db(self):
        """テーブルが存在しない場合のみ作成する。"""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS devices (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    host        TEXT NOT NULL,
                    ip          TEXT NOT NULL UNIQUE,
                    type        TEXT DEFAULT 'Unknown',
                    grp         TEXT DEFAULT 'Default',
                    timeout_sec REAL DEFAULT 1.0,
                    retry_count INTEGER DEFAULT 3,
                    tcp_port    INTEGER DEFAULT 80,
                    notes       TEXT DEFAULT '',
                    created_at  TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS ping_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id   INTEGER NOT NULL,
                    checked_at  TEXT DEFAULT (datetime('now','localtime')),
                    status      TEXT NOT NULL,
                    latency_ms  REAL,
                    error_type  TEXT,
                    method      TEXT DEFAULT 'icmp',
                    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_history_device
                    ON ping_history(device_id, checked_at);

                CREATE TABLE IF NOT EXISTS alert_state (
                    device_id    INTEGER PRIMARY KEY,
                    last_status  TEXT,
                    last_alerted TEXT,
                    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
                );
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ─────────────────────────────────────────────
    # デバイス CRUD
    # ─────────────────────────────────────────────

    def seed_defaults(self, devices: list[dict]):
        """初回起動時のみデフォルト機器を登録する。"""
        with self._conn() as conn:
            existing = {row["ip"] for row in conn.execute("SELECT ip FROM devices").fetchall()}
            for d in devices:
                if d["ip"] not in existing:
                    conn.execute(
                        """INSERT INTO devices (host, ip, type, grp, timeout_sec, retry_count)
                           VALUES (?,?,?,?,?,?)""",
                        (d["host"], d["ip"], d.get("type", "Unknown"),
                         d.get("group", "Default"),
                         d.get("timeout", 1.0), d.get("retry", 3))
                    )

    def get_all_devices(self) -> pd.DataFrame:
        """全機器をDataFrameで返す。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, host, ip, type, grp, timeout_sec, retry_count, tcp_port, notes "
                "FROM devices ORDER BY grp, host"
            ).fetchall()
        if not rows:
            return pd.DataFrame(columns=["id","host","ip","type","grp","timeout_sec","retry_count","tcp_port","notes"])
        return pd.DataFrame([dict(r) for r in rows])

    def upsert_device(self, host: str, ip: str, device_type: str, group: str,
                      timeout: float, retry: int, tcp_port: int = 80,
                      notes: str = "", device_id: Optional[int] = None) -> int:
        """機器を追加または更新する。device_id が None なら INSERT。"""
        with self._conn() as conn:
            if device_id:
                conn.execute(
                    """UPDATE devices SET host=?, ip=?, type=?, grp=?, timeout_sec=?,
                       retry_count=?, tcp_port=?, notes=? WHERE id=?""",
                    (host, ip, device_type, group, timeout, retry, tcp_port, notes, device_id)
                )
                return device_id
            else:
                cur = conn.execute(
                    """INSERT OR REPLACE INTO devices
                       (host, ip, type, grp, timeout_sec, retry_count, tcp_port, notes)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (host, ip, device_type, group, timeout, retry, tcp_port, notes)
                )
                return cur.lastrowid

    def delete_device(self, device_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM devices WHERE id=?", (device_id,))

    def get_groups(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT DISTINCT grp FROM devices ORDER BY grp").fetchall()
        return [r["grp"] for r in rows]

    # ─────────────────────────────────────────────
    # Ping履歴
    # ─────────────────────────────────────────────

    def record_ping(self, device_id: int, status: str,
                    latency_ms: Optional[float], error_type: Optional[str] = None,
                    method: str = "icmp"):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO ping_history (device_id, status, latency_ms, error_type, method)
                   VALUES (?,?,?,?,?)""",
                (device_id, status, latency_ms, error_type, method)
            )

    def get_history(self, device_id: int, hours: int = 24) -> pd.DataFrame:
        """指定機器の直近N時間の履歴をDataFrameで返す。"""
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT checked_at, status, latency_ms, error_type, method
                   FROM ping_history
                   WHERE device_id=? AND checked_at >= ?
                   ORDER BY checked_at""",
                (device_id, since)
            ).fetchall()
        if not rows:
            return pd.DataFrame(columns=["checked_at","status","latency_ms","error_type","method"])
        df = pd.DataFrame([dict(r) for r in rows])
        df["checked_at"] = pd.to_datetime(df["checked_at"])
        return df

    def get_all_history(self, hours: int = 24) -> pd.DataFrame:
        """全機器の履歴をJOINして返す。"""
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT d.host, d.ip, d.grp, h.checked_at, h.status, h.latency_ms, h.error_type, h.method
                   FROM ping_history h
                   JOIN devices d ON d.id = h.device_id
                   WHERE h.checked_at >= ?
                   ORDER BY h.checked_at DESC""",
                (since,)
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["checked_at"] = pd.to_datetime(df["checked_at"])
        return df

    def export_history_csv(self, hours: int = 24) -> str:
        """履歴をCSV文字列として返す。"""
        df = self.get_all_history(hours=hours)
        if df.empty:
            return "host,ip,grp,checked_at,status,latency_ms,error_type,method\n"
        return df.to_csv(index=False)

    # ─────────────────────────────────────────────
    # 稼働率 (Uptime)
    # ─────────────────────────────────────────────

    def get_uptime(self, device_id: int, hours: int = 24) -> Optional[float]:
        """
        直近N時間の稼働率(%)を計算する。
        履歴なしの場合は None を返す。
        """
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status FROM ping_history WHERE device_id=? AND checked_at >= ?",
                (device_id, since)
            ).fetchall()
        if not rows:
            return None
        total = len(rows)
        online = sum(1 for r in rows if r["status"] == "Online")
        return round(online / total * 100, 2)

    def get_uptime_all(self, hours: int = 24) -> dict[int, Optional[float]]:
        """全機器の稼働率を {device_id: uptime%} の辞書で返す。"""
        with self._conn() as conn:
            device_ids = [r["id"] for r in conn.execute("SELECT id FROM devices").fetchall()]
        return {did: self.get_uptime(did, hours) for did in device_ids}

    # ─────────────────────────────────────────────
    # アラート状態管理
    # ─────────────────────────────────────────────

    def get_alert_state(self, device_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_status, last_alerted FROM alert_state WHERE device_id=?",
                (device_id,)
            ).fetchone()
        return dict(row) if row else {"last_status": None, "last_alerted": None}

    def update_alert_state(self, device_id: int, status: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO alert_state (device_id, last_status, last_alerted)
                   VALUES (?,?,?)
                   ON CONFLICT(device_id) DO UPDATE SET last_status=excluded.last_status,
                   last_alerted=excluded.last_alerted""",
                (device_id, status, now)
            )

    def should_alert(self, device_id: int, new_status: str, cooldown_seconds: int = 300) -> bool:
        """
        通知すべきかを判定する:
        - ステータスが変化した場合（Online→Offline, Offline→Online）
        - クールダウン期間を超えた場合
        """
        state = self.get_alert_state(device_id)
        if state["last_status"] == new_status:
            # ステータス変化なし
            if state["last_alerted"] is None:
                return False
            last = datetime.strptime(state["last_alerted"], "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - last).total_seconds() > cooldown_seconds
        # ステータス変化あり → 必ず通知
        return True

    # ─────────────────────────────────────────────
    # デバイスCSVインポート
    # ─────────────────────────────────────────────

    def import_devices_csv(self, csv_content: str) -> tuple[int, list[str]]:
        """
        CSV文字列から機器一覧をインポートする。
        必須列: host, ip
        任意列: type, group, timeout, retry, notes

        Returns:
            (追加数, エラーメッセージリスト)
        """
        import io
        from .validator import validate_ip_or_hostname, validate_timeout, validate_retry

        try:
            df = pd.read_csv(io.StringIO(csv_content))
        except Exception as e:
            return 0, [f"CSV読み込みエラー: {e}"]

        required = {"host", "ip"}
        missing = required - set(df.columns)
        if missing:
            return 0, [f"必須列が不足しています: {missing}"]

        added = 0
        errors = []
        for i, row in df.iterrows():
            ip = str(row.get("ip", "")).strip()
            ok, err = validate_ip_or_hostname(ip)
            if not ok:
                errors.append(f"行{i+2}: {err}")
                continue
            self.upsert_device(
                host=str(row.get("host", ip)),
                ip=ip,
                device_type=str(row.get("type", "Unknown")),
                group=str(row.get("group", "Default")),
                timeout=float(row.get("timeout", 1.0)),
                retry=int(row.get("retry", 3)),
                notes=str(row.get("notes", "")),
            )
            added += 1
        return added, errors
