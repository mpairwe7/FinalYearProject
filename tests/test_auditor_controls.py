"""Auditor controls: separation of duties, the staff-action trail, and its integrity.

Found by the QA audit of the auditor dashboard on 2026-09-29:

* the ticket console hid its controls from auditors, but
  ``PATCH /v1/admin/tickets/{id}`` accepted an auditor's status change,
  reassignment or reply to the taxpayer;
* staff actions (ticket updates, flag toggles, override edits) and staff reads
  of a taxpayer's transcript were not recorded anywhere;
* the tamper-evident audit ledger existed with a verifier, but no endpoint
  let an auditor read it or check it.

and by the follow-up review of the ledger itself:

* only the payload was hashed, so a row's actor could be swapped unnoticed;
* nothing ever sealed the chain, and verify ignored seals, so a range
  rewritten with every hash recomputed still verified;
* reading or checking the trail left no trace of who did it.

Each test uses its own tenant, so its hash chain is its own and a deliberate
tamper never leaves the shared analytics database broken for the next run.
"""

from __future__ import annotations

import os
import sys
import types
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "App" / "backend"))

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")
os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:1")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from App.backend.app import database as db  # noqa: E402
from App.backend.app import main as main_module  # noqa: E402
from App.backend.app.audit.ledger import get_ledger  # noqa: E402
from App.backend.app.auth.jwt_auth import make_dev_token  # noqa: E402
from App.backend.app.flags import flags  # noqa: E402
from App.backend.app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    db.init_db()
    app.state.model = types.SimpleNamespace(name="stub")
    app.state.speech = types.SimpleNamespace(enabled=False)
    return TestClient(app)


@pytest.fixture
def tenant():
    return f"audit-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def ledger_on():
    before = flags.is_enabled("audit_ledger")
    flags.set("audit_ledger", True)
    yield True
    flags.set("audit_ledger", before)


def _headers(user: str, role: str, tenant: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_dev_token(user, tenant_id=tenant, role=role)}"}


def _ticket() -> str:
    return db.create_ticket(reason="QA audit fixture", user_query="How do I file my return?")["id"]


def test_an_auditor_cannot_change_a_ticket_but_an_officer_can(client, tenant):
    ticket_id = _ticket()
    auditor = _headers("aud-1", "ura_auditor", tenant)
    officer = _headers("off-1", "ura_staff", tenant)
    refused = client.patch(f"/v1/admin/tickets/{ticket_id}", headers=auditor, json={"status": "resolved"})
    assert refused.status_code == 403
    assert db.get_ticket(ticket_id)["status"] != "resolved"
    assert client.post(f"/v1/admin/tickets/{ticket_id}/presence", headers=auditor).status_code == 403
    # Reading is still allowed: auditing means seeing the case.
    assert client.get(f"/v1/admin/tickets/{ticket_id}", headers=auditor).status_code == 200
    accepted = client.patch(f"/v1/admin/tickets/{ticket_id}", headers=officer, json={"status": "assigned"})
    assert accepted.status_code == 200


def test_staff_actions_and_transcript_reads_are_recorded_without_content(client, tenant, ledger_on):
    ticket_id = _ticket()
    officer = _headers("off-2", "ura_staff", tenant)
    auditor = _headers("aud-2", "ura_auditor", tenant)
    client.get(f"/v1/admin/tickets/{ticket_id}", headers=auditor)
    client.patch(
        f"/v1/admin/tickets/{ticket_id}",
        headers=officer,
        json={"status": "assigned", "officer_reply": "Please file by the 15th."},
    )
    events = client.get("/v1/admin/audit/events?event_type=staff.", headers=auditor).json()["events"]
    by_type = {e["event_type"]: e for e in events}
    viewed = by_type["staff.ticket_viewed"]
    assert viewed["actor"] == "aud-2"
    assert viewed["payload"]["ticket_id"] == ticket_id
    updated = by_type["staff.ticket_updated"]
    assert updated["actor"] == "off-2"
    assert updated["payload"]["actor_role"] == "ura_staff"
    assert updated["payload"]["status"] == "assigned"
    assert updated["payload"]["officer_reply_chars"] == len("Please file by the 15th.")
    assert "Please file" not in str(updated["payload"])


def test_flag_changes_are_recorded(client, tenant, ledger_on):
    admin = _headers("adm-1", "ura_admin", tenant)
    name = "hyde"
    before = flags.is_enabled(name)
    try:
        assert client.patch(f"/v1/admin/flags/{name}?enabled=true", headers=admin).status_code == 200
        assert client.delete(f"/v1/admin/flags/{name}", headers=admin).status_code == 200
    finally:
        flags.set(name, before)
    events = client.get("/v1/admin/audit/events?event_type=staff.flag", headers=admin).json()["events"]
    assert [e["event_type"] for e in events] == ["staff.flag_cleared", "staff.flag_set"]
    assert events[1]["payload"] == {"actor_role": "ura_admin", "flag": name, "enabled": True}


def test_auditor_reads_the_trail_and_filters_it(client, tenant, ledger_on):
    auditor = _headers("aud-3", "ura_auditor", tenant)
    officer = _headers("off-3", "ura_staff", tenant)
    assert client.get("/v1/admin/audit/events").status_code in (401, 403, 503)
    assert client.get("/v1/admin/audit/events", headers=officer).status_code == 403
    for _ in range(3):
        client.get(f"/v1/admin/tickets/{_ticket()}", headers=officer)
    client.get(f"/v1/admin/tickets/{_ticket()}", headers=auditor)

    page = client.get("/v1/admin/audit/events?limit=2", headers=auditor).json()
    assert page["ledger_enabled"] is True
    assert len(page["events"]) == 2
    assert page["events"][0]["seq"] > page["events"][1]["seq"]  # newest first
    older = client.get(
        f"/v1/admin/audit/events?limit=2&before_seq={page['next_before_seq']}", headers=auditor
    ).json()
    assert all(e["seq"] < page["events"][-1]["seq"] for e in older["events"])

    mine = client.get("/v1/admin/audit/events?actor=aud-3", headers=auditor).json()["events"]
    assert {e["actor"] for e in mine} == {"aud-3"}
    # The ticket aud-3 opened, and aud-3's first-page read of the trail; the
    # read of an older page is not recorded again.
    assert [e["event_type"] for e in mine] == ["audit.trail_viewed", "staff.ticket_viewed"]
    assert client.get("/v1/admin/audit/events?event_type=DROP%20TABLE", headers=auditor).status_code == 422


def test_verify_detects_a_tampered_row(client, tenant, ledger_on):
    auditor = _headers("aud-4", "ura_auditor", tenant)
    for _ in range(3):
        client.get(f"/v1/admin/tickets/{_ticket()}", headers=auditor)
    clean = client.get("/v1/admin/audit/verify", headers=auditor).json()
    assert clean["valid"] is True
    assert (clean["scope"], clean["rows_checked"], clean["unsealed_rows"]) == ("full", 3, 3)

    middle = client.get("/v1/admin/audit/events?limit=3", headers=auditor).json()["events"][1]
    db.execute(
        "UPDATE audit_events SET payload = ? WHERE event_id = ?",
        ('{"actor_role": "ura_auditor", "ticket_id": "edited"}', middle["event_id"]),
    )
    broken = client.get("/v1/admin/audit/verify", headers=auditor).json()
    assert broken["valid"] is False
    assert broken["breaks"][0]["seq"] == middle["seq"]
    assert broken["breaks"][0]["reason"] == "payload_hash mismatch"


def test_reattributing_a_row_is_detected(client, tenant, ledger_on):
    auditor = _headers("aud-5", "ura_auditor", tenant)
    officer = _headers("off-5", "ura_staff", tenant)
    client.patch(f"/v1/admin/tickets/{_ticket()}", headers=officer, json={"status": "assigned"})
    row = client.get("/v1/admin/audit/events?event_type=staff.ticket_updated", headers=auditor).json()["events"][0]
    db.execute("UPDATE audit_events SET user_id = ? WHERE event_id = ?", ("someone-else", row["event_id"]))
    report = client.get("/v1/admin/audit/verify", headers=auditor).json()
    assert report["valid"] is False
    assert report["breaks"][0] == {"seq": row["seq"], "event_id": row["event_id"], "reason": "row_hash mismatch"}


def test_reading_and_checking_the_trail_is_recorded(client, tenant, ledger_on):
    auditor = _headers("aud-6", "ura_auditor", tenant)
    client.get("/v1/admin/audit/events?event_type=staff.&actor=off-9", headers=auditor)
    client.get("/v1/admin/audit/verify", headers=auditor)
    events = client.get("/v1/admin/audit/events?event_type=audit.", headers=auditor).json()["events"]
    verified, viewed = events[0], events[1]
    assert (verified["event_type"], verified["actor"]) == ("audit.chain_verified", "aud-6")
    assert verified["payload"]["valid"] is True
    assert (viewed["event_type"], viewed["actor"]) == ("audit.trail_viewed", "aud-6")
    assert viewed["payload"]["event_type"] == "staff."
    assert viewed["payload"]["actor"] == "off-9"


def test_sealing_is_for_audit_readers_and_catches_a_rewrite(client, tenant):
    auditor = _headers("aud-7", "ura_auditor", tenant)
    officer = _headers("off-7", "ura_staff", tenant)
    ledger = get_ledger()
    for i in range(4):
        ledger.append("staff.ticket_updated", {"status": f"s{i}"}, tenant_id=tenant, user_id="off-7")
    assert client.post("/v1/admin/audit/seal", headers=officer).status_code == 403
    sealed = client.post("/v1/admin/audit/seal", headers=auditor).json()
    assert sealed["sealed"] is True
    assert (sealed["anchor"]["first_seq"], sealed["anchor"]["last_seq"]) == (1, 4)
    assert sealed["anchor"]["head_hash"]
    assert client.post("/v1/admin/audit/seal", headers=auditor).json() == {"sealed": False, "anchor": None}

    # Rewrite row 2 and recompute every hash after it: the chain alone agrees.
    rows = db.query_all("SELECT * FROM audit_events WHERE tenant_id = ? ORDER BY seq", (tenant,))
    from App.backend.app.audit.ledger import chain_hash, envelope_hash
    from App.backend.app.audit.merkle import sha256_hex

    prev = rows[0]["row_hash"]
    for row in rows[1:]:
        payload = '{"status": "rewritten"}' if row["seq"] == 2 else row["payload"]
        payload_hash = sha256_hex(payload)
        envelope = envelope_hash(
            event_id=row["event_id"], event_type=row["event_type"], tenant_id=tenant,
            user_id=row["user_id"], ts=row["ts"], seq=row["seq"],
        )
        row_hash = chain_hash(prev, payload_hash, envelope, row["hash_version"])
        db.execute(
            "UPDATE audit_events SET payload = ?, prev_hash = ?, payload_hash = ?, row_hash = ? WHERE event_id = ?",
            (payload, prev, payload_hash, row_hash, row["event_id"]),
        )
        prev = row_hash
    report = client.get("/v1/admin/audit/verify", headers=auditor).json()
    assert report["breaks"] == []
    assert report["valid"] is False
    assert report["anchor_breaks"][0]["reason"].startswith("merkle_root mismatch")


def test_a_large_ledger_is_checked_from_its_newest_seal(client, tenant, monkeypatch):
    auditor = _headers("aud-8", "ura_auditor", tenant)
    ledger = get_ledger()
    for i in range(3):
        ledger.append("generate", {"n": i}, tenant_id=tenant)
    ledger.seal_pending(tenant)
    ledger.append("generate", {"n": 3}, tenant_id=tenant)
    monkeypatch.setattr(main_module, "_AUDIT_VERIFY_FULL_MAX_ROWS", 2)
    auto = client.get("/v1/admin/audit/verify", headers=auditor).json()
    assert (auto["scope"], auto["rows_checked"], auto["anchors_checked"]) == ("since_seal", 1, 1)
    assert auto["unsealed_rows"] == 1
    forced = client.get("/v1/admin/audit/verify?scope=full", headers=auditor).json()
    assert (forced["scope"], forced["rows_checked"]) == ("full", 4)
    assert client.get("/v1/admin/audit/verify?scope=everything", headers=auditor).status_code == 422


def test_the_schedule_seals_every_tenant_only_while_the_ledger_is_on(tenant):
    ledger = get_ledger()
    ledger.append("generate", {"n": 1}, tenant_id=tenant)
    before = flags.is_enabled("audit_ledger")
    try:
        flags.set("audit_ledger", False)
        assert main_module._seal_audit_ledgers() == 0
        assert ledger.latest_anchor(tenant) is None
        flags.set("audit_ledger", True)
        assert main_module._seal_audit_ledgers() >= 1
        assert ledger.latest_anchor(tenant)["last_seq"] == 1
    finally:
        flags.set("audit_ledger", before)


def test_one_tenant_failing_to_seal_does_not_leave_the_others_unsealed(tenant, monkeypatch):
    ledger = get_ledger()
    ledger.append("generate", {"n": 1}, tenant_id=tenant)
    real = ledger.seal_pending

    def flaky(tenant_id="default"):
        if tenant_id != tenant:
            raise RuntimeError("simulated failure for another tenant")
        return real(tenant_id)

    monkeypatch.setattr(ledger, "tenants", lambda: ["broken-tenant", tenant])
    monkeypatch.setattr(ledger, "seal_pending", flaky)
    before = flags.is_enabled("audit_ledger")
    try:
        flags.set("audit_ledger", True)
        assert main_module._seal_audit_ledgers() == 1
    finally:
        flags.set("audit_ledger", before)
    assert ledger.latest_anchor(tenant)["last_seq"] == 1



# ---------------------------------------------------------------------------
# QA pass after #515 (call desk): completeness, not just the routes we know of
# ---------------------------------------------------------------------------
#: Writes an auditor may make: adding a seal changes no row.
_AUDITOR_MAY_WRITE = {("POST", "/v1/admin/audit/seal")}
_WRITE_BODIES = {
    "/v1/admin/overrides": {"query": "What is VAT?", "reply": "18%."},
    "/v1/admin/calls/{call_id}/review": {"rating": 4, "note": "fine"},
    "/v1/admin/calls/{call_id}/hold": {"on": True},
    "/v1/admin/calls/{call_id}/transfer": {"team": "disputes"},
    "/v1/admin/calls/{call_id}/wrapup": {"outcome": "resolved", "note": "done"},
    "/v1/admin/calls/{call_id}/callback-done": {"note": "called back"},
    "/v1/admin/officers/me/presence": {"status": "available"},
    "/v1/admin/tickets/{ticket_id}": {"status": "resolved"},
    # A valid body, so the request reaches the role check rather than a 422.
    "/v1/admin/outbox/test": {"channel": "email", "recipient": "qa@example.org", "message": "matrix"},
}
_WRITE_PARAMS = {"/v1/admin/flags/{name}": {"enabled": "true"}}


def _receptionist_call(tenant: str) -> str:
    from App.backend.app.receptionist.store import create_call, init_receptionist_schema
    from App.backend.app.voice_consent import init_voice_consent_schema

    init_receptionist_schema()
    init_voice_consent_schema()
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    create_call(call_id, conversation_id=f"conv_{call_id}", status="transferring", tenant_id=tenant)
    return call_id


def test_every_admin_write_route_refuses_an_auditor(client, tenant):
    """Enumerates the live routes, so a new staff write that forgets its role check fails here."""
    from fastapi.routing import APIRoute

    ids = {
        "ticket_id": _ticket(),
        "call_id": _receptionist_call(tenant),
        "override_id": "ovr-qa",
        "name": "hyde",
        "report_id": "kb_test123",
        "tombstone_id": "tomb_test123",
        "precedence_id": "prec_test123",
        "notification_id": "ntf_test123",
    }
    auditor = _headers("aud-matrix", "ura_auditor", tenant)
    checked, leaks = [], []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/v1/admin"):
            continue
        for method in sorted(route.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            if (method, route.path) in _AUDITOR_MAY_WRITE:
                continue
            response = client.request(
                method,
                route.path.format(**ids),
                headers=auditor,
                json=_WRITE_BODIES.get(route.path, {}),
                params=_WRITE_PARAMS.get(route.path),
            )
            checked.append(f"{method} {route.path}")
            if response.status_code != 403:
                leaks.append(f"{method} {route.path} -> {response.status_code}")
    assert leaks == []
    assert len(checked) >= 15, checked  # 15 on 2026-09-29; the matrix grows with the API


def test_call_reads_and_reviews_are_recorded_with_actor_and_role(client, tenant, ledger_on):
    call_id = _receptionist_call(tenant)
    auditor = _headers("aud-calls", "ura_auditor", tenant)
    officer = _headers("off-calls", "ura_staff", tenant)
    assert client.get(f"/v1/admin/calls/{call_id}", headers=auditor).status_code == 200
    assert client.post(f"/v1/admin/calls/{call_id}/review", headers=auditor, json={"rating": 1}).status_code == 403
    note = "Clear and polite, confirmed the TIN."
    reviewed = client.post(f"/v1/admin/calls/{call_id}/review", headers=officer, json={"rating": 4, "note": note})
    assert reviewed.status_code == 200

    events = client.get("/v1/admin/audit/events?limit=20", headers=auditor).json()["events"]
    by_type = {e["event_type"]: e for e in events}
    viewed = by_type["voice_staff_viewed_call"]
    assert (viewed["actor"], viewed["payload"]["actor_role"]) == ("aud-calls", "ura_auditor")
    assert viewed["payload"]["session_id"] == call_id
    review = by_type["staff.call_reviewed"]
    assert review["actor"] == "off-calls"
    assert review["payload"] == {"actor_role": "ura_staff", "call_id": call_id, "rating": 4, "note_chars": len(note)}


def test_every_voice_event_the_code_writes_is_a_known_type():
    """An unknown type still writes, but logs a warning on every call-desk action."""
    import ast

    from App.backend.app.voice_consent import VOICE_EVENT_TYPES

    def constants(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.IfExp):
            return constants(node.body) + constants(node.orelse)
        return []

    used = set()
    for path in (PROJECT_ROOT / "App" / "backend" / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name == "log_voice_event":
                for kw in node.keywords:
                    if kw.arg == "event_type":
                        used.update(constants(kw.value))
            elif name == "_staff_call_event" and len(node.args) >= 3:
                used.update(constants(node.args[2]))
    assert used, "the scan found no voice events at all"
    assert used - VOICE_EVENT_TYPES == set()
