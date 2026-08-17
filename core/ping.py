"""
core/ping.py — 高度な死活監視エンジン

機能:
  1. ICMPによるPing（subprocess / ネイティブコマンド）
  2. 並列実行（ThreadPoolExecutor）
  3. リトライ判定（fail_threshold 回以上失敗でOffline）
  4. TCP/ソケットフォールバック（ICMP禁止環境対応）
  5. SNMP基本クエリ（sysDescr OID）
  6. 詳細エラー分類（timeout / nxdomain / permission_denied / unknown）
"""
import re
import socket
import subprocess
import platform
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────
# 結果データクラス
# ─────────────────────────────────────────────────────

@dataclass
class PingResult:
    host: str
    ip: str
    status: str                          # "Online" | "Offline"
    latency_ms: Optional[float] = None
    error_type: Optional[str] = None     # "timeout" | "nxdomain" | "permission_denied" | "unknown"
    method: str = "icmp"                 # "icmp" | "tcp" | "snmp"
    attempts: int = 0
    failures: int = 0
    device_id: Optional[int] = None


# ─────────────────────────────────────────────────────
# メインクラス
# ─────────────────────────────────────────────────────

class PingChecker:
    def __init__(self, config: dict):
        ping_cfg = config.get("ping", {})
        self.default_count    = int(ping_cfg.get("default_count", 3))
        self.default_timeout  = float(ping_cfg.get("default_timeout", 1.0))
        self.fail_threshold   = int(ping_cfg.get("fail_threshold", 2))
        self.tcp_fallback     = bool(ping_cfg.get("tcp_fallback", True))
        self.snmp_enabled     = bool(ping_cfg.get("snmp", {}).get("enabled", False))
        self.snmp_community   = str(ping_cfg.get("snmp", {}).get("community", "public"))
        self.snmp_port        = int(ping_cfg.get("snmp", {}).get("port", 161))
        self.snmp_timeout     = int(ping_cfg.get("snmp", {}).get("timeout", 2))
        self._os = platform.system().lower()

    # ── 単体Ping ──────────────────────────────────────

    def ping_one(self, ip: str, count: int = None, timeout: float = None) -> PingResult:
        """
        単一IPへICMP Pingを送信する（リトライ判定あり）。
        fail_threshold 回以上失敗したら Offline。
        """
        count   = count   or self.default_count
        timeout = timeout or self.default_timeout

        failures   = 0
        latencies  = []
        last_error = None

        for _ in range(count):
            ok, lat, err = self._ping_once(ip, timeout)
            if ok:
                if lat is not None:
                    latencies.append(lat)
            else:
                failures += 1
                last_error = err

        if failures >= self.fail_threshold:
            # ICMPが全滅→TCPフォールバック
            if self.tcp_fallback and last_error not in ("permission_denied",):
                return self._tcp_fallback(ip, latencies, count, failures, last_error)
            return PingResult(
                host=ip, ip=ip,
                status="Offline",
                latency_ms=None,
                error_type=last_error,
                method="icmp",
                attempts=count,
                failures=failures,
            )

        avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else None
        return PingResult(
            host=ip, ip=ip,
            status="Online",
            latency_ms=avg_lat,
            method="icmp",
            attempts=count,
            failures=failures,
        )

    def _ping_once(self, ip: str, timeout: float) -> tuple[bool, Optional[float], Optional[str]]:
        """
        OSネイティブのpingコマンドを1回実行。
        Returns: (success, latency_ms_or_None, error_type_or_None)
        """
        if self._os == "windows":
            cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
        else:
            cmd = ["ping", "-c", "1", "-W", str(int(timeout)), ip]

        try:
            t0 = time.perf_counter()
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout + 1,
            )
            elapsed = (time.perf_counter() - t0) * 1000  # ms

            if result.returncode == 0:
                output = result.stdout.decode(errors="ignore")
                lat = self._parse_latency(output, elapsed)
                return True, lat, None
            else:
                err_out = result.stderr.decode(errors="ignore") + result.stdout.decode(errors="ignore")
                return False, None, self._classify_error(err_out)

        except subprocess.TimeoutExpired:
            return False, None, "timeout"
        except PermissionError:
            return False, None, "permission_denied"
        except FileNotFoundError:
            return False, None, "permission_denied"
        except Exception as e:
            return False, None, "unknown"

    def _parse_latency(self, output: str, fallback: float) -> float:
        """Ping出力から平均レイテンシを抽出する。"""
        if self._os == "windows":
            # 日本語OS: "平均 = 3ms" / English: "Average = 3ms"
            m = re.search(r"(?:Average|平均)\s*=\s*(\d+(?:\.\d+)?)ms", output, re.IGNORECASE)
            if m:
                return float(m.group(1))
            # 単一パケット "時間 =3ms" / "time=3ms"
            m = re.search(r"(?:時間|time)\s*[=<]\s*(\d+(?:\.\d+)?)ms", output, re.IGNORECASE)
            if m:
                return float(m.group(1))
        else:
            m = re.search(r"[\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms", output)
            if m:
                return float(m.group(1))
            m = re.search(r"time=([\d.]+) ms", output)
            if m:
                return float(m.group(1))
        return round(fallback, 2)

    def _classify_error(self, output: str) -> str:
        """エラー出力から原因を分類する。"""
        out_lower = output.lower()
        if any(kw in out_lower for kw in ["timeout", "timed out", "タイムアウト", "要求がタイムアウト"]):
            return "timeout"
        if any(kw in out_lower for kw in ["could not find", "name or service not known",
                                            "no such host", "名前解決", "unknown host"]):
            return "nxdomain"
        if any(kw in out_lower for kw in ["permission", "operation not permitted",
                                            "access denied", "アクセス拒否"]):
            return "permission_denied"
        return "unknown"

    # ── TCP フォールバック ─────────────────────────────

    def _tcp_fallback(self, ip: str, icmp_latencies: list,
                      attempts: int, failures: int, icmp_error: str) -> PingResult:
        """ICMP失敗時、TCPポート80/443への接続を試みる。"""
        for port in [80, 443, 22]:
            ok, lat = self._tcp_connect(ip, port, timeout=2.0)
            if ok:
                return PingResult(
                    host=ip, ip=ip,
                    status="Online",
                    latency_ms=lat,
                    method=f"tcp:{port}",
                    attempts=attempts,
                    failures=failures,
                )
        return PingResult(
            host=ip, ip=ip,
            status="Offline",
            latency_ms=None,
            error_type=icmp_error or "timeout",
            method="tcp",
            attempts=attempts,
            failures=failures,
        )

    def _tcp_connect(self, ip: str, port: int, timeout: float) -> tuple[bool, Optional[float]]:
        try:
            t0 = time.perf_counter()
            with socket.create_connection((ip, port), timeout=timeout):
                lat = round((time.perf_counter() - t0) * 1000, 2)
                return True, lat
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False, None

    # ── SNMP ─────────────────────────────────────────

    def snmp_query(self, ip: str) -> PingResult:
        """
        SNMP v2c で sysDescr (1.3.6.1.2.1.1.1.0) を取得する。
        pysnmp が利用可能な場合のみ動作する。
        """
        try:
            from pysnmp.hlapi import (
                getCmd, SnmpEngine, CommunityData, UdpTransportTarget,
                ContextData, ObjectType, ObjectIdentity
            )
            t0 = time.perf_counter()
            error_indication, error_status, _, var_binds = next(
                getCmd(
                    SnmpEngine(),
                    CommunityData(self.snmp_community),
                    UdpTransportTarget((ip, self.snmp_port), timeout=self.snmp_timeout, retries=1),
                    ContextData(),
                    ObjectType(ObjectIdentity("1.3.6.1.2.1.1.1.0")),  # sysDescr
                )
            )
            lat = round((time.perf_counter() - t0) * 1000, 2)
            if error_indication:
                return PingResult(host=ip, ip=ip, status="Offline",
                                  error_type="snmp_error", method="snmp")
            return PingResult(host=ip, ip=ip, status="Online",
                              latency_ms=lat, method="snmp")
        except ImportError:
            return PingResult(host=ip, ip=ip, status="Offline",
                              error_type="snmp_unavailable", method="snmp")
        except Exception:
            return PingResult(host=ip, ip=ip, status="Offline",
                              error_type="snmp_error", method="snmp")

    # ── 並列実行 ──────────────────────────────────────

    def ping_all(self, devices: list[dict], max_workers: int = 16) -> list[PingResult]:
        """
        全機器へ並列にPingを送信する。
        devices: [{"id", "host", "ip", "timeout_sec", "retry_count"}, ...]
        """
        results = []

        def _check(device: dict) -> PingResult:
            ip      = device.get("ip", "")
            count   = int(device.get("retry_count", self.default_count))
            timeout = float(device.get("timeout_sec", self.default_timeout))

            if self.snmp_enabled:
                r = self.snmp_query(ip)
            else:
                r = self.ping_one(ip, count=count, timeout=timeout)

            r.host      = device.get("host", ip)
            r.device_id = device.get("id")
            return r

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_check, d): d for d in devices}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    d = futures[future]
                    results.append(PingResult(
                        host=d.get("host", d.get("ip", "?")),
                        ip=d.get("ip", "?"),
                        status="Offline",
                        error_type="unknown",
                        device_id=d.get("id"),
                    ))
        return results
