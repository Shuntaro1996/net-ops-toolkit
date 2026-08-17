"""
tests/test_database.py — core.database の単体テスト
"""
import os
import pytest
import tempfile
from core.database import Database


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = Database(db_path)
    yield db
    try:
        os.remove(db_path)
    except Exception:
        pass


def test_seed_and_get_devices(temp_db):
    defaults = [
        {"host": "Router-01", "ip": "192.168.1.1", "type": "Router", "group": "Core"},
        {"host": "Switch-01", "ip": "192.168.1.2", "type": "Switch", "group": "Core"},
    ]
    temp_db.seed_defaults(defaults)
    df = temp_db.get_all_devices()
    assert len(df) == 2
    assert "Router-01" in df["host"].values


def test_record_ping_and_uptime(temp_db):
    temp_db.upsert_device("Host-A", "10.0.0.1", "Server", "Test", 1.0, 3)
    df = temp_db.get_all_devices()
    dev_id = int(df[df["ip"] == "10.0.0.1"]["id"].values[0])

    temp_db.record_ping(dev_id, "Online", 5.2, None, "icmp")
    temp_db.record_ping(dev_id, "Online", 4.8, None, "icmp")
    temp_db.record_ping(dev_id, "Offline", None, "timeout", "icmp")

    uptime = temp_db.get_uptime(dev_id, hours=24)
    assert uptime is not None
    assert round(uptime, 1) == 66.7


def test_get_latest_status_all(temp_db):
    temp_db.upsert_device("Host-A", "10.0.0.1", "Server", "Test", 1.0, 3)
    temp_db.upsert_device("Host-B", "10.0.0.2", "Server", "Test", 1.0, 3)
    df = temp_db.get_all_devices()
    did_a = int(df[df["ip"] == "10.0.0.1"]["id"].values[0])

    temp_db.record_ping(did_a, "Online", 3.0, None, "icmp")

    latest = temp_db.get_latest_status_all()
    assert len(latest) == 2
    row_a = latest[latest["ip"] == "10.0.0.1"].iloc[0]
    row_b = latest[latest["ip"] == "10.0.0.2"].iloc[0]
    assert row_a["status"] == "Online"
    assert row_b["status"] == "—"


def test_export_prometheus_metrics(temp_db):
    temp_db.upsert_device("Host-A", "10.0.0.1", "Server", "Test", 1.0, 3)
    df = temp_db.get_all_devices()
    did = int(df[df["ip"] == "10.0.0.1"]["id"].values[0])
    temp_db.record_ping(did, "Online", 2.5, None, "icmp")

    metrics = temp_db.export_prometheus_metrics()
    assert "netops_device_up" in metrics
    assert 'host="Host-A"' in metrics
    assert "netops_device_latency_ms" in metrics
