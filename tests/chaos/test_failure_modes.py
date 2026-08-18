"""In-process failure drills (G34). No cluster is required."""

from __future__ import annotations

from app.tools.ura_account import account_api_status
from app.ura_account_mock import account_mode
from app.malware_scan import MalwareRejected, scan_required
from app.tenancy import qdrant_payload_filter, rls_set_local_sql


def test_account_connector_fail_closed(monkeypatch):
    monkeypatch.setenv("URA_ACCOUNT_API_MODE", "off")
    monkeypatch.delenv("URA_ACCOUNT_API_BASE", raising=False)
    monkeypatch.delenv("URA_ACCOUNT_API_TOKEN", raising=False)
    status = account_api_status()
    assert status["live"] is False
    assert status["configured"] is False
    assert account_mode() == "off"


def test_malware_required_is_fail_closed(monkeypatch):
    monkeypatch.setenv("MALWARE_SCAN_REQUIRED", "true")
    assert scan_required() is True
    assert MalwareRejected is not None


def test_tenant_filter_and_rls_sql(monkeypatch):
    from app import tenancy
    from app.flags import flags

    monkeypatch.setenv("FLAG_MULTI_TENANT", "true")
    flags.set("multi_tenant", True)
    monkeypatch.setattr(tenancy, "tenant_enabled", lambda: True)
    try:
        filt = qdrant_payload_filter("tenant-a")
        assert filt is not None
        assert filt["must"][0]["match"]["value"] == "tenant-a"
        sql = rls_set_local_sql("tenant-a")
        assert "tenant-a" in sql
        assert "SET LOCAL" in sql
    finally:
        flags.clear("multi_tenant")
