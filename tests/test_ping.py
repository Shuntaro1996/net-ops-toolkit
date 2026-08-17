"""
tests/test_ping.py — core.ping の単体テスト

実際のネットワーク疎通なしでロジックをテストするため、
subprocess.run をモック化する。
"""
import pytest
from unittest.mock import MagicMock, patch
from core.ping import PingChecker, PingResult

BASE_CONFIG = {
    "ping": {
        "default_count": 3,
        "default_timeout": 1.0,
        "fail_threshold": 2,
        "tcp_fallback": True,
        "snmp": {"enabled": False},
    }
}


@pytest.fixture
def checker():
    return PingChecker(BASE_CONFIG)


# ─────────────────────────────────────────────────────
# _parse_latency テスト
# ─────────────────────────────────────────────────────

class TestParseLatency:
    def test_windows_average_en(self, checker):
        output = "Minimum = 1ms, Maximum = 5ms, Average = 3ms"
        lat = checker._parse_latency(output, 99.0)
        assert lat == 3.0

    def test_windows_average_ja(self, checker):
        output = "最小 = 1ms、最大 = 5ms、平均 = 3ms"
        lat = checker._parse_latency(output, 99.0)
        assert lat == 3.0

    def test_linux_rtt(self, checker):
        checker._os = "linux"
        output = "rtt min/avg/max/mdev = 0.200/2.500/5.000/1.200 ms"
        lat = checker._parse_latency(output, 99.0)
        assert lat == 2.5

    def test_fallback(self, checker):
        lat = checker._parse_latency("no match here", 42.0)
        assert lat == 42.0


# ─────────────────────────────────────────────────────
# _classify_error テスト
# ─────────────────────────────────────────────────────

class TestClassifyError:
    def test_timeout(self, checker):
        assert checker._classify_error("Request timed out.") == "timeout"
        assert checker._classify_error("要求がタイムアウトしました") == "timeout"

    def test_nxdomain(self, checker):
        assert checker._classify_error("could not find host") == "nxdomain"
        assert checker._classify_error("Name or service not known") == "nxdomain"

    def test_permission(self, checker):
        assert checker._classify_error("Operation not permitted") == "permission_denied"
        assert checker._classify_error("Access denied") == "permission_denied"

    def test_unknown(self, checker):
        assert checker._classify_error("something weird happened") == "unknown"


# ─────────────────────────────────────────────────────
# ping_one テスト（subprocess モック）
# ─────────────────────────────────────────────────────

class TestPingOne:
    def _make_proc(self, returncode: int, stdout: str = "", stderr: str = ""):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout.encode()
        proc.stderr = stderr.encode()
        return proc

    def test_online(self, checker):
        proc = self._make_proc(0, "Average = 5ms")
        with patch("subprocess.run", return_value=proc):
            result = checker.ping_one("192.168.1.1", count=3, timeout=1.0)
        assert result.status == "Online"
        assert result.latency_ms == 5.0

    def test_offline_all_fail(self, checker):
        proc = self._make_proc(1, "", "Request timed out.")
        with patch("subprocess.run", return_value=proc), \
             patch.object(checker, "_tcp_connect", return_value=(False, None)):
            result = checker.ping_one("192.168.1.99", count=3, timeout=0.5)
        assert result.status == "Offline"

    def test_tcp_fallback_on_icmp_failure(self, checker):
        proc = self._make_proc(1, "", "Request timed out.")
        with patch("subprocess.run", return_value=proc), \
             patch.object(checker, "_tcp_connect", return_value=(True, 8.5)):
            result = checker.ping_one("192.168.1.99", count=3, timeout=0.5)
        assert result.status == "Online"
        assert result.latency_ms == 8.5
        assert "tcp" in result.method

    def test_threshold_not_reached(self, checker):
        """fail_threshold=2: 1回だけ失敗ならOnline"""
        responses = [
            self._make_proc(1, "", "timeout"),
            self._make_proc(0, "Average = 3ms"),
            self._make_proc(0, "Average = 3ms"),
        ]
        with patch("subprocess.run", side_effect=responses):
            result = checker.ping_one("192.168.1.1", count=3, timeout=1.0)
        assert result.status == "Online"


# ─────────────────────────────────────────────────────
# ping_all 並列テスト
# ─────────────────────────────────────────────────────

class TestPingAll:
    def test_parallel_returns_all(self, checker):
        devices = [
            {"id": 1, "host": "Router-01", "ip": "192.168.1.1",  "retry_count": 3, "timeout_sec": 1.0},
            {"id": 2, "host": "Switch-A",  "ip": "192.168.1.10", "retry_count": 3, "timeout_sec": 1.0},
            {"id": 3, "host": "AP-1F",     "ip": "192.168.1.50", "retry_count": 3, "timeout_sec": 1.0},
        ]
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = b"Average = 2ms"
        proc.stderr = b""

        with patch("subprocess.run", return_value=proc):
            results = checker.ping_all(devices, max_workers=4)

        assert len(results) == 3
        assert all(r.status == "Online" for r in results)

    def test_parallel_handles_exception(self, checker):
        """subprocessとTCPの両方が失敗した場合、Offlineを返すことを確認する。"""
        devices = [{"id": 1, "host": "BadHost", "ip": "0.0.0.1",
                    "retry_count": 1, "timeout_sec": 0.1}]
        # ping_one自体をモックしてOffline結果を返す
        from core.ping import PingResult
        offline_result = PingResult(host="BadHost", ip="0.0.0.1", status="Offline",
                                    error_type="unknown", device_id=1)
        with patch.object(checker, "ping_one", return_value=offline_result):
            results = checker.ping_all(devices)
        assert len(results) == 1
        assert results[0].status == "Offline"
