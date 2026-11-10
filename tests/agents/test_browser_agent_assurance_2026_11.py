"""Browser agent assurance, November 2026 (ADR-0038, G541-G552).

The portal co-pilot is the assistant's browser agent: it reads DOM telemetry
from the taxpayer's browser, returns ``dom_actions`` and runs a portal task
once a person allows it. Each test here reproduces a gap from the audit
(``docs/plans/browser-agent-assurance-2026-11-10.md``) before its fix.
"""

from __future__ import annotations

from typing import Any

import pytest
from app import database as db
from app import portal_copilot as pc
from app.audit.ledger import get_ledger, reset_ledger
from app.flags import flags
from app.main import app, limiter
from app.service import ChatModel
from fastapi.testclient import TestClient

EXECUTE = "/v1/portal/tasks/execute"
PRN_FIELDS = {
    "tin": "1000023456",
    "tax_head": "VAT_STANDARD",
    "amount_ugx": "250000",
    "payment_mode": "COMMERCIAL_BANK",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_db: Any) -> TestClient:
    monkeypatch.setenv("MCP_REQUEST_STATE_SECRET", "test-secret-for-browser-agent-assurance-2026")
    flags.set("portal_copilot", True)
    # A fresh limiter window: this file alone makes more calls than one allows.
    limiter.reset()
    reset_ledger()
    pc.clear_portal_task_cache()
    prev_model = getattr(app.state, "model", None)
    prev_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    app.state.model = ChatModel()
    yield TestClient(app)
    app.state.model = prev_model
    app.dependency_overrides.clear()
    app.dependency_overrides.update(prev_overrides)
    flags.clear("portal_copilot")
    flags.clear("audit_ledger")
    pc.clear_portal_task_cache()
    reset_ledger()


def _mint(
    client: TestClient,
    *,
    conversation: str,
    session: str | None = "sess-taxpayer",
    task_id: str = "payment_generate_prn",
    fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Submit a task form over ``/v1/chat`` and return the card it answers with."""
    response = client.post(
        "/v1/chat",
        json={
            "message": "Submitted the form",
            "conversation_id": conversation,
            "portal_task_submission": {"task_id": task_id, "fields": dict(fields or PRN_FIELDS)},
        },
        headers={"X-Session-ID": session} if session else {},
    )
    assert response.status_code == 200, response.text
    return response.json()["portal_copilot"]


def _decide(
    client: TestClient,
    card: dict[str, Any],
    *,
    conversation: str,
    decision: str = "allow",
    session: str | None = "sess-taxpayer",
    digest: str | None = "from-card",
) -> Any:
    body: dict[str, Any] = {
        "decision": decision,
        "request_state": card["request_state"],
        "idempotency_key": card["idempotency_key"],
        "conversation_id": conversation,
    }
    if digest == "from-card":
        body["displayed_fields_sha256"] = card["displayed_fields_sha256"]
    elif digest is not None:
        body["displayed_fields_sha256"] = digest
    return client.post(EXECUTE, json=body, headers={"X-Session-ID": session} if session else {})


def _forget_in_process_state() -> None:
    """Leave only what a second uvicorn worker would have: the shared store."""
    with pc._TASK_CACHE_LOCK:
        pc._IN_FLIGHT_TASK_KEYS.clear()
        pc._EXECUTED_TASK_CACHE.clear()
        pc._DECLINED_TASK_CACHE.clear()


class _Connector:
    """Stands in for the portal connector and counts how often it is reached."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, during: Any = None, ok: bool = True) -> None:
        self.calls: list[str] = []
        self.arguments: list[dict[str, Any]] = []
        self.options: list[dict[str, Any]] = []
        self._during = during
        self._ok = ok
        monkeypatch.setattr(pc.ToolRegistry, "call", self)

    def __call__(self, name: str, arguments: dict[str, Any], **options: Any) -> dict[str, Any]:
        self.calls.append(name)
        self.arguments.append(dict(arguments))
        self.options.append(options)
        if self._during is not None and len(self.calls) == 1:
            self._during()
        if not self._ok:
            return {"ok": False, "error": "The portal did not accept the request.", "live": False}
        return {"ok": True, "prn": f"22600000000{len(self.calls)}", "live": False}


# ---------------------------------------------------------------------------
# G541: an allowed task runs once, whichever worker hears the click
# ---------------------------------------------------------------------------
class TestExactlyOnceAcrossWorkers:
    def test_the_claim_goes_to_one_caller(self, tmp_db: Any) -> None:
        assert db.claim_workflow_session("portal_task_decision:abc", "portal_task_decision", lease_seconds=900)
        assert not db.claim_workflow_session("portal_task_decision:abc", "portal_task_decision", lease_seconds=900)

    def test_a_lapsed_claim_can_be_taken_again(self, tmp_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        assert db.claim_workflow_session("portal_task_decision:abc", "portal_task_decision", lease_seconds=900)
        later = pc.time.time() + 901
        monkeypatch.setattr(db.time, "time", lambda: later)
        assert db.claim_workflow_session("portal_task_decision:abc", "portal_task_decision", lease_seconds=900)

    def test_a_claim_does_not_cross_tenants(self, tmp_db: Any) -> None:
        assert db.claim_workflow_session("portal_task_decision:abc", "portal_task_decision", lease_seconds=900)
        assert not db.claim_workflow_session(
            "portal_task_decision:abc", "portal_task_decision", lease_seconds=900, tenant_id="other"
        )

    def test_a_second_worker_does_not_run_the_task_again(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        card = _mint(client, conversation="conv-twice")
        seen: dict[str, Any] = {}

        def second_worker_hears_the_same_click() -> None:
            _forget_in_process_state()
            seen["second"] = _decide(client, card, conversation="conv-twice")

        connector = _Connector(monkeypatch, during=second_worker_hears_the_same_click)
        first = _decide(client, card, conversation="conv-twice")

        assert first.status_code == 200
        assert first.json()["status"] == "executed"
        assert len(connector.calls) == 1, "the connector ran once per worker, not once per approval"
        assert seen["second"].status_code == 409

    def test_a_later_worker_replays_the_first_receipt(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        card = _mint(client, conversation="conv-replay")
        connector = _Connector(monkeypatch)
        first = _decide(client, card, conversation="conv-replay").json()
        _forget_in_process_state()
        second = _decide(client, card, conversation="conv-replay").json()

        assert len(connector.calls) == 1
        assert second["idempotent_replay"] is True
        assert second["result"]["prn"] == first["result"]["prn"]

    def test_nothing_runs_when_the_claim_cannot_be_recorded(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        card = _mint(client, conversation="conv-store-down")
        connector = _Connector(monkeypatch)

        def store_down(*_: Any, **__: Any) -> bool:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(db, "claim_workflow_session", store_down)
        response = _decide(client, card, conversation="conv-store-down")

        assert response.status_code == 503
        assert connector.calls == []

    def test_a_refused_task_can_be_tried_again(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        card = _mint(client, conversation="conv-retry")
        failing = _Connector(monkeypatch, ok=False)
        assert _decide(client, card, conversation="conv-retry").json()["status"] == "error"
        assert len(failing.calls) == 1

        working = _Connector(monkeypatch)
        _forget_in_process_state()
        retry = _decide(client, card, conversation="conv-retry")
        assert retry.json()["status"] == "executed"
        assert len(working.calls) == 1

    def test_a_task_being_run_cannot_be_declined(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        card = _mint(client, conversation="conv-decline-late")
        seen: dict[str, Any] = {}

        def taxpayer_clicks_disallow_on_another_worker() -> None:
            _forget_in_process_state()
            seen["decline"] = _decide(client, card, conversation="conv-decline-late", decision="disallow")

        _Connector(monkeypatch, during=taxpayer_clicks_disallow_on_another_worker)
        allowed = _decide(client, card, conversation="conv-decline-late")

        assert allowed.json()["status"] == "executed"
        assert seen["decline"].status_code == 409, "it said nothing was sent while the task was being sent"


# ---------------------------------------------------------------------------
# G542: an approval proves a session and proves what was on the card
# ---------------------------------------------------------------------------
class TestApprovalIsBoundToASessionAndTheDisplay:
    def test_allow_without_the_display_digest_is_refused(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        card = _mint(client, conversation="conv-no-digest")
        connector = _Connector(monkeypatch)
        response = _decide(client, card, conversation="conv-no-digest", digest=None)

        assert response.status_code == 400
        assert "displayed_fields_sha256" in response.json()["detail"]
        assert connector.calls == []

    def test_no_confirmation_is_signed_for_a_caller_without_a_session(self, client: TestClient) -> None:
        card = _mint(client, conversation="conv-no-session", session=None)

        assert card["card_type"] == "session_required"
        assert "request_state" not in card

    def test_a_decision_without_a_session_is_refused(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        card = _mint(client, conversation="conv-drop-session")
        connector = _Connector(monkeypatch)
        response = _decide(client, card, conversation="conv-drop-session", session=None)

        assert response.status_code == 403
        assert connector.calls == []

    def test_a_card_signed_for_a_signed_in_taxpayer_is_not_honoured_signed_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_db: Any
    ) -> None:
        monkeypatch.setenv("MCP_REQUEST_STATE_SECRET", "test-secret-for-browser-agent-assurance-2026")
        pc.clear_portal_task_cache()
        owner = db.conversation_state_key("conv-out", session_id="sess-1", user_id="1000023456")
        card = pc.build_portal_task_card(
            "payment_generate_prn",
            extracted_fields=PRN_FIELDS,
            user_id="1000023456",
            session_id="sess-1",
            conversation_id="conv-out",
        )
        with pytest.raises(pc.PortalTaskOwnershipError):
            pc.handle_portal_task_decision(
                "allow",
                card["request_state"],
                card["idempotency_key"],
                owner_key=owner,
                user_id=None,
                session_id="sess-1",
                conversation_id="conv-out",
                displayed_fields_sha256=card["displayed_fields_sha256"],
            )


# ---------------------------------------------------------------------------
# G543: the agent does not invent what the taxpayer did not say
# ---------------------------------------------------------------------------
TIN_APPLICATION = {
    "nin": "CM90100101ABCD",
    "surname": "Okello",
    "given_name": "James",
    "mobile_number": "+256772123456",
    "email": "james@example.org",
}


class TestNoInventedValues:
    def test_a_missing_date_of_birth_is_asked_for(self, client: TestClient) -> None:
        card = _mint(client, conversation="conv-dob", task_id="tin_apply_individual", fields=TIN_APPLICATION)

        assert card["card_type"] == "inline_form"
        assert "date_of_birth" in card["missing_fields"]

    def test_no_identity_field_carries_a_default(self) -> None:
        identity = {"nin", "date_of_birth", "surname", "given_name", "full_name", "mobile_number", "email"}
        invented = [
            f"{spec.task_id}.{field.name}={field.default_value}"
            for spec in pc.PORTAL_TASK_CATALOG.values()
            for field in spec.fields
            if field.name in identity and field.default_value
        ]
        assert invented == []

    def test_a_value_the_taxpayer_did_not_give_is_marked_assumed(self, client: TestClient) -> None:
        fields = {"tin": "1000023456", "amount_ugx": "250000"}
        card = _mint(client, conversation="conv-assumed", fields=fields)

        assert card["card_type"] == "confirmation_required"
        by_name = {row["name"]: row for row in card["display_fields"]}
        assert by_name["tax_head"]["assumed"] is True
        assert by_name["payment_mode"]["assumed"] is True
        assert by_name["tin"]["assumed"] is False
        assert by_name["amount_ugx"]["assumed"] is False
        assert set(card["assumed_fields"]) >= {"tax_head", "payment_mode"}

    def test_a_value_the_taxpayer_gave_is_not_marked_assumed(self, client: TestClient) -> None:
        card = _mint(client, conversation="conv-stated")

        by_name = {row["name"]: row for row in card["display_fields"]}
        assert by_name["tax_head"]["assumed"] is False
        assert by_name["payment_mode"]["assumed"] is False


class TestTheConnectorCallSaysWhatHappened:
    def test_a_lookup_nobody_confirmed_is_not_recorded_as_confirmed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connector = _Connector(monkeypatch)
        card = _mint(client, conversation="conv-lookup", task_id="tin_search_verify", fields={"tin": "1000023456"})

        assert card["card_type"] == "executed_result"
        assert connector.options[0]["caller_confirmed"] is False

    def test_an_allowed_task_is_recorded_as_confirmed(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        card = _mint(client, conversation="conv-confirmed")
        connector = _Connector(monkeypatch)
        _decide(client, card, conversation="conv-confirmed")

        assert connector.options[0]["caller_confirmed"] is True

    def test_the_action_is_recorded_under_the_callers_tenant(
        self, monkeypatch: pytest.MonkeyPatch, tmp_db: Any
    ) -> None:
        monkeypatch.setenv("MCP_REQUEST_STATE_SECRET", "test-secret-for-browser-agent-assurance-2026")
        pc.clear_portal_task_cache()
        connector = _Connector(monkeypatch)
        owner = db.conversation_state_key("conv-tenant", session_id="sess-1", user_id=None, tenant_id="kcca")
        card = pc.build_portal_task_card(
            "payment_generate_prn",
            extracted_fields=PRN_FIELDS,
            session_id="sess-1",
            conversation_id="conv-tenant",
            tenant_id="kcca",
        )
        pc.handle_portal_task_decision(
            "allow",
            card["request_state"],
            card["idempotency_key"],
            owner_key=owner,
            session_id="sess-1",
            conversation_id="conv-tenant",
            tenant_id="kcca",
            displayed_fields_sha256=card["displayed_fields_sha256"],
        )

        assert connector.options[0]["tenant_id"] == "kcca"

    def test_only_a_low_risk_read_runs_without_confirmation(self) -> None:
        pc._ensure_connector_tools_registered()
        unsafe = []
        for spec in pc.PORTAL_TASK_CATALOG.values():
            if spec.tier is not pc.PortalTaskTier.AUTONOMOUS_VERIFY:
                continue
            tool = pc.ToolRegistry.get(spec.tool_name)
            if tool is None or tool.schema.risk != "low" or not tool.schema.read_only:
                unsafe.append(spec.task_id)
        assert unsafe == []

    def test_the_connector_is_not_handed_an_identity_the_taxpayer_did_not_give(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connector = _Connector(monkeypatch)
        pc.execute_portal_task("tin_apply_individual", {"nin": "CM90100101ABCD", "surname": "Okello"})
        pc.execute_portal_task("tin_apply_non_individual", {"ursb_brn": "80020001234567", "legal_name": "Acme"})

        sent = " ".join(str(value) for arguments in connector.arguments for value in arguments.values())
        for invented in ("1992-01-01", "+256700000000", "taxpayer@example.ug", "info@company.co.ug", "Applicant"):
            assert invented not in sent


# ---------------------------------------------------------------------------
# G541: every decision reaches the ledger, and an approval reaches it first
# ---------------------------------------------------------------------------
def _ledger_rows() -> list[dict[str, Any]]:
    rows = get_ledger().read(tenant_id="default", limit=200)
    return [row for row in rows if str(row["event_type"]).startswith("portal_task_")]


class TestDecisionsReachTheLedger:
    def test_an_allowed_task_leaves_an_approval_and_an_outcome(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flags.set("audit_ledger", True)
        card = _mint(client, conversation="conv-ledger")
        seen: dict[str, Any] = {}
        _Connector(monkeypatch, during=lambda: seen.update(before=[r["event_type"] for r in _ledger_rows()]))
        response = _decide(client, card, conversation="conv-ledger")

        assert response.json()["status"] == "executed"
        assert seen["before"] == ["portal_task_approved"], "the portal heard of the approval before the ledger did"
        assert [row["event_type"] for row in _ledger_rows()] == ["portal_task_approved", "portal_task_executed"]

    def test_a_declined_task_is_recorded(self, client: TestClient) -> None:
        flags.set("audit_ledger", True)
        card = _mint(client, conversation="conv-ledger-decline")
        _decide(client, card, conversation="conv-ledger-decline", decision="disallow")

        assert [row["event_type"] for row in _ledger_rows()] == ["portal_task_declined"]

    def test_nothing_runs_when_the_approval_cannot_be_recorded(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flags.set("audit_ledger", True)
        card = _mint(client, conversation="conv-ledger-down")
        connector = _Connector(monkeypatch)

        def ledger_down(*_: Any, **__: Any) -> Any:
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(get_ledger(), "append", ledger_down)
        response = _decide(client, card, conversation="conv-ledger-down")

        assert response.status_code == 503
        assert connector.calls == []

    def test_a_refused_approval_can_be_tried_again_once_the_ledger_is_back(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flags.set("audit_ledger", True)
        card = _mint(client, conversation="conv-ledger-back")
        connector = _Connector(monkeypatch)
        ledger = get_ledger()
        real_append = ledger.append

        def ledger_down(*_: Any, **__: Any) -> Any:
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(ledger, "append", ledger_down)
        assert _decide(client, card, conversation="conv-ledger-back").status_code == 503
        monkeypatch.setattr(ledger, "append", real_append)
        _forget_in_process_state()

        assert _decide(client, card, conversation="conv-ledger-back").json()["status"] == "executed"
        assert len(connector.calls) == 1

    def test_the_approval_row_carries_the_evidence_of_the_confirmation(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flags.set("audit_ledger", True)
        card = _mint(client, conversation="conv-evidence")
        _Connector(monkeypatch)
        _decide(client, card, conversation="conv-evidence")

        approved = next(row for row in _ledger_rows() if row["event_type"] == "portal_task_approved")
        evidence = approved["payload"]
        assert evidence["display_digest_verified"] is True
        assert evidence["session_bound"] is True
        assert isinstance(evidence["review_ms"], int)
        assert evidence["review_ms"] >= 0
        assert evidence["params_sha256"]
        assert evidence["idempotency_key"] == card["idempotency_key"]
        assert "1000023456" not in str(evidence), "the ledger keeps digests, never the taxpayer's values"

    def test_with_the_ledger_off_a_task_still_runs(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        flags.set("audit_ledger", False)
        card = _mint(client, conversation="conv-ledger-off")
        connector = _Connector(monkeypatch)

        assert _decide(client, card, conversation="conv-ledger-off").json()["status"] == "executed"
        assert len(connector.calls) == 1
        assert _ledger_rows() == []
