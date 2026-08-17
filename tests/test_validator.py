"""
tests/test_validator.py — core.validator の単体テスト
"""
import pytest
from core.validator import (
    validate_ip,
    validate_hostname,
    validate_ip_or_hostname,
    validate_timeout,
    validate_retry,
    validate_device_row,
)


class TestValidateIP:
    def test_valid_ips(self):
        for ip in ["192.168.1.1", "10.0.0.1", "172.16.254.1", "0.0.0.0", "255.255.255.255"]:
            ok, err = validate_ip(ip)
            assert ok, f"{ip} should be valid, got: {err}"

    def test_invalid_ips(self):
        for ip in ["999.999.999.999", "192.168.1", "abc", "", "192.168.1.256", "1.2.3.4.5"]:
            ok, _ = validate_ip(ip)
            assert not ok, f"{ip} should be invalid"

    def test_none_input(self):
        ok, msg = validate_ip(None)
        assert not ok
        assert "空" in msg

    def test_whitespace_stripped(self):
        ok, _ = validate_ip("  192.168.1.1  ")
        assert ok


class TestValidateHostname:
    def test_valid_hostnames(self):
        for h in ["router-01", "switch.local", "AP-Floor-1F", "a", "host123"]:
            ok, err = validate_hostname(h)
            assert ok, f"{h} should be valid, got: {err}"

    def test_invalid_hostnames(self):
        for h in ["", "router..local", "-invalid", "host_name", "a" * 254]:
            ok, _ = validate_hostname(h)
            assert not ok, f"'{h}' should be invalid"


class TestValidateIPOrHostname:
    def test_accepts_ip(self):
        ok, _ = validate_ip_or_hostname("192.168.1.1")
        assert ok

    def test_accepts_hostname(self):
        ok, _ = validate_ip_or_hostname("router.local")
        assert ok

    def test_rejects_invalid(self):
        ok, _ = validate_ip_or_hostname("!!invalid!!")
        assert not ok


class TestValidateTimeout:
    def test_valid_range(self):
        for t in [0.1, 1.0, 5.0, 30.0]:
            ok, _ = validate_timeout(t)
            assert ok, f"timeout={t} should be valid"

    def test_out_of_range(self):
        for t in [0.0, -1, 30.1, 100]:
            ok, _ = validate_timeout(t)
            assert not ok, f"timeout={t} should be invalid"

    def test_non_numeric(self):
        ok, _ = validate_timeout("abc")
        assert not ok


class TestValidateRetry:
    def test_valid_range(self):
        for r in [1, 3, 5, 10]:
            ok, _ = validate_retry(r)
            assert ok

    def test_out_of_range(self):
        for r in [0, 11, -1]:
            ok, _ = validate_retry(r)
            assert not ok


class TestValidateDeviceRow:
    def test_all_valid(self):
        errors = validate_device_row("Router-01", "192.168.1.1", 1.0, 3)
        assert errors == []

    def test_empty_host(self):
        errors = validate_device_row("", "192.168.1.1", 1.0, 3)
        assert any("Host" in e for e in errors)

    def test_invalid_ip(self):
        # IPでもホスト名でも無効な文字列（スペース・特殊文字を含む）
        errors = validate_device_row("Router", "192.168 .1.1", 1.0, 3)
        assert len(errors) > 0

    def test_multiple_errors(self):
        errors = validate_device_row("", "bad-ip", 0.0, 0)
        assert len(errors) >= 3
