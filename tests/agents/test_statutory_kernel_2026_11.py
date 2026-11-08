"""The Statutory Knowledge Kernel, phase 1 (ADR-0033).

Coverage of every rate-table key, the generated schema (Neo4j Ontology Graph
Specification and SHACL), runtime invariants, content-addressed releases,
kernel functions, per-turn pinning and approval sealing, the action contract
and source precedence. Every check also runs against a broken graph, so a
validator that always passes cannot pass these tests.
"""

from __future__ import annotations

import copy
import datetime as _dt
import time

import pytest
from app.agents.graphs.runtime import END, GraphRuntime, InMemoryCheckpointStore, NodeResult
from app.agents.graphs.state import AgentGraphState, GraphOutcome, TaxpayerActionRequest
from app.graph.build import (
    _legacy_tax_for_key,
    all_table_keys,
    build_graph,
    is_threshold,
    kind_for_key,
    unclassified_keys,
)
from app.graph.schema import FACT_KINDS, EdgeKind, NodeKind, fact_id
from app.graph.store import InMemoryGraphStore
from app.kernel import functions, precedence, release
from app.kernel.contract import UNDECLARED_TOOL, undeclared_concepts, undeclared_tools
from app.kernel.schema_export import shacl_validate, to_ogs, validate_ogs
from app.kernel.validate import check
from app.tax import tables


@pytest.fixture(scope="module")
def store() -> InMemoryGraphStore:
    return build_graph()


def _mutated(store: InMemoryGraphStore, mutate) -> InMemoryGraphStore:
    raw = copy.deepcopy(store.to_dict())
    mutate(raw)
    return InMemoryGraphStore.from_dict(raw)


def _codes(violations) -> set[str]:
    return {v.code for v in violations}


# --------------------------------------------------------------------------- coverage


def test_every_rate_table_key_reaches_the_kernel() -> None:
    """Before ADR-0033, 68 of 102 keys were dropped, every deadline and penalty among them."""
    assert unclassified_keys() == []
    assert len(all_table_keys()) >= 102


def test_each_key_has_one_node_per_year_with_the_tables_value(store: InMemoryGraphStore) -> None:
    for fy in tables.list_fiscal_years():
        for key, value in tables.get_table(fy).rates.items():
            node = store.get(fact_id(kind_for_key(key), key, fy))
            assert node is not None, (key, fy)
            assert node.props["value"] == value


def test_keys_the_graph_already_had_keep_their_kind_and_id(store: InMemoryGraphStore) -> None:
    legacy = [k for k in all_table_keys() if _legacy_tax_for_key(k)]
    assert len(legacy) == 34
    for key in legacy:
        expected = NodeKind.THRESHOLD if is_threshold(key) else NodeKind.RATE
        assert kind_for_key(key) is expected


@pytest.mark.parametrize(
    ("key", "kind"),
    [
        ("paye_due_date_monthly", NodeKind.OBLIGATION),
        ("objection_timeline_days", NodeKind.OBLIGATION),
        ("penal_tax_late_filing_monthly_rate", NodeKind.PENALTY),
        ("late_payment_interest_monthly_rate", NodeKind.PENALTY),
        ("efris_penalty_failure_to_issue_invoice", NodeKind.PENALTY),
        ("ursb_name_reservation_fee_ugx", NodeKind.FEE),
        ("fx_conversion_rule", NodeKind.PARAMETER),
        ("bik_loan_statutory_interest_rate", NodeKind.RATE),
    ],
)
def test_new_keys_get_the_right_kind(key: str, kind: NodeKind) -> None:
    assert kind_for_key(key) is kind


def test_a_figure_with_no_legal_basis_is_never_served_as_verified(store: InMemoryGraphStore) -> None:
    node = store.get(fact_id(NodeKind.OBLIGATION, "paye_due_date_monthly", "FY2026-27"))
    assert node.props["uncited"] is True
    assert node.unverified is True
    for kind in FACT_KINDS:
        for n in store.by_kind(kind):
            if not n.props.get("legal_basis"):
                assert n.unverified, n.id
                assert n.props["uncited"], n.id


def test_the_multi_hop_shadow_set_is_unchanged() -> None:
    from app.graph.shadow import score

    report = score()
    assert report["correct"] == report["total"] == 12


# --------------------------------------------------------------------------- schema


def test_the_schema_document_validates_against_the_published_specification() -> None:
    assert validate_ogs(to_ogs()) == []


def test_the_specification_validator_rejects_what_it_should() -> None:
    doc = to_ogs()
    broken = copy.deepcopy(doc)
    broken["unexpected"] = True
    assert validate_ogs(broken)
    broken = copy.deepcopy(doc)
    broken["relationships"]["rate_rated_for_tax_type"].pop("to")
    broken["relationships"]["rate_rated_for_tax_type"]["bogus"] = 1
    assert validate_ogs(broken)


def test_the_graph_conforms_to_the_shacl_shapes(store: InMemoryGraphStore) -> None:
    pytest.importorskip("pyshacl")
    conforms, report = shacl_validate(store)
    assert conforms, report[:2000]


@pytest.mark.parametrize("breakage", ["uncited_and_verified", "two_taxes", "unknown_property"])
def test_shacl_catches_a_broken_graph(store: InMemoryGraphStore, breakage: str) -> None:
    pytest.importorskip("pyshacl")
    target = fact_id(NodeKind.RATE, "vat_standard", "FY2026-27")

    def mutate(raw: dict) -> None:
        node = next(n for n in raw["nodes"] if n["id"] == target)
        if breakage == "uncited_and_verified":
            raw["edges"] = [e for e in raw["edges"] if not (e["src"] == target and e["kind"] == "imposed_by")]
            node["unverified"] = False
        elif breakage == "two_taxes":
            raw["edges"].append({"src": target, "kind": "rated_for", "dst": "tax:paye", "props": {}})
        else:
            node["props"]["made_up"] = "x"

    conforms, _report = shacl_validate(_mutated(store, mutate))
    assert not conforms


# --------------------------------------------------------------------------- invariants


def test_a_clean_build_has_no_violations(store: InMemoryGraphStore) -> None:
    assert check(store) == []


def test_each_invariant_fires_on_its_breakage(store: InMemoryGraphStore) -> None:
    vat = fact_id(NodeKind.RATE, "vat_standard", "FY2026-27")
    vat_old = fact_id(NodeKind.RATE, "vat_standard", "FY2025-26")

    def uncite(raw):
        raw["edges"] = [e for e in raw["edges"] if not (e["src"] == vat and e["kind"] == "imposed_by")]
        next(n for n in raw["nodes"] if n["id"] == vat)["unverified"] = False

    def second_tax(raw):
        raw["edges"].append({"src": vat, "kind": "rated_for", "dst": "tax:paye", "props": {}})

    def cycle(raw):
        raw["edges"].append({"src": vat_old, "kind": "supersedes", "dst": vat, "props": {}})

    def overlap(raw):
        next(n for n in raw["nodes"] if n["id"] == "fy:FY2025-26")["props"]["effective_to"] = "2026-12-31"

    def wrong_value(raw):
        next(n for n in raw["nodes"] if n["id"] == vat)["props"]["value"] = 0.16

    def drop_node(raw):
        raw["nodes"] = [n for n in raw["nodes"] if n["id"] != vat]
        raw["edges"] = [e for e in raw["edges"] if vat not in (e["src"], e["dst"])]

    def bad_year(raw):
        next(n for n in raw["nodes"] if n["id"] == vat)["props"]["fiscal_year"] = "2026"

    cases = {"provenance": uncite, "cardinality": second_tax, "supersedes_cycle": cycle, "overlap": overlap,
             "parity": wrong_value, "completeness": drop_node, "pattern": bad_year}
    for code, mutate in cases.items():
        assert code in _codes(check(_mutated(store, mutate))), code


# --------------------------------------------------------------------------- releases


def test_the_running_kernel_is_a_recorded_release() -> None:
    version = release.compute()
    assert version.released, "rate tables, build or rules changed: record a release"
    assert version.version.count(".") == 2


def test_a_content_change_is_unreleased_until_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "content_sha256", lambda store: "0" * 64)
    version = release.compute()
    assert not version.released
    assert "+unreleased." in version.version


def test_a_release_must_be_newer_and_new(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    registry = tmp_path / "releases.json"
    registry.write_text(release.REGISTRY.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(release, "REGISTRY", registry)
    latest = release.load_registry()[-1]["version"]
    with pytest.raises(ValueError, match="not newer"):
        release.record(latest, "again")
    with pytest.raises(ValueError, match="already released"):
        release.record("99.0.0", "same content")


def test_an_approval_needs_a_named_reviewer() -> None:
    assert release.proposed_entry("9.9.9", "x")["review"]["status"] == release.REVIEW_BASELINE
    approved = release.proposed_entry("9.9.9", "x", reviewer="A. Officer", role="Commissioner Tax Policy")
    assert approved["review"]["status"] == release.REVIEW_APPROVED


# --------------------------------------------------------------------------- functions


def test_a_figure_is_the_one_in_force_on_the_date() -> None:
    june = functions.fact("vat_registration_threshold_annual", _dt.date(2026, 6, 30))
    july = functions.fact("vat_registration_threshold_annual", _dt.date(2026, 7, 1))
    assert june.fiscal_year == "FY2025-26"
    assert july.fiscal_year == "FY2026-27"
    assert july.provision
    assert july.provision_id.startswith("prov:")


@pytest.mark.parametrize(
    ("key", "day", "reason"),
    [
        ("not_a_real_key", _dt.date(2026, 8, 1), "undeclared"),
        ("vat_standard", _dt.date(2020, 1, 1), "no_fiscal_year"),
        ("efris_penal_tax_minimum_ugx", _dt.date(2026, 3, 1), "not_in_year"),
    ],
)
def test_the_kernel_says_what_it_does_not_know(key: str, day: _dt.date, reason: str) -> None:
    result = functions.fact(key, day)
    assert isinstance(result, functions.NotInKernel)
    assert result.reason == reason


def test_deadlines_and_penalties_come_with_their_provisions() -> None:
    day = _dt.date(2026, 8, 15)
    due = {f.key: f for f in functions.obligations(as_of=day)}
    assert due["excise_due_date_monthly"].value == 15
    assert "Excise Duty Act" in due["excise_due_date_monthly"].provision
    penal = {f.key: f for f in functions.penalties(as_of=day, tax="tax_procedures")}
    assert penal["penal_tax_late_filing_minimum_ugx"].value == 200000
    assert "s.49(1)" in penal["penal_tax_late_filing_minimum_ugx"].provision


def test_explain_lists_what_a_provision_imposes() -> None:
    fact = functions.fact("late_payment_interest_monthly_rate", _dt.date(2026, 8, 15))
    imposed = functions.explain(fact.provision_id)
    assert any(f.key == "late_payment_interest_monthly_rate" for f in imposed)
    assert isinstance(functions.explain("prov:nothing-here"), functions.NotInKernel)


def test_kernel_tools_answer_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools import ToolRegistry

    tool = ToolRegistry.get("kernel_fact")
    monkeypatch.delenv("FLAG_TAX_GRAPH", raising=False)
    assert tool.execute(key="vat_standard")["ok"] is False
    monkeypatch.setenv("FLAG_TAX_GRAPH", "true")
    result = tool.execute(key="vat_standard", date="2026-08-15")
    assert result["ok"]
    assert result["facts"][0]["value"] == 0.18
    assert tool.execute(key="vat_standard", date="15/08/2026")["ok"] is False


# --------------------------------------------------------------------------- pinning and sealing


def test_a_turn_records_the_kernel_it_answered_under() -> None:
    from app.audit import turns
    from app.kernel.pin import pin_for_turn
    from app.turn_context import ACTIVE_KERNEL_PIN

    pin = pin_for_turn(_dt.date(2026, 8, 15)).to_dict()
    token = ACTIVE_KERNEL_PIN.set(pin)
    try:
        payload = turns.build_turn_payload(message="What is VAT?", result={"reply": "18%"}, channel="rest")
    finally:
        ACTIVE_KERNEL_PIN.reset(token)
    assert payload["schema"] == 4
    assert payload["kernel_pin"]["kernel_version"] == release.current().version
    assert payload["kernel_pin"]["as_of"] == "2026-08-15"


def _approval_graph(store) -> GraphRuntime:
    def gate(s: AgentGraphState) -> NodeResult:
        if s.pending_action and s.pending_action.requires_consent and not s.pending_action.authorized:
            s.outcome = GraphOutcome.WAITING_USER
            return NodeResult(next_node="act", outcome=GraphOutcome.WAITING_USER)
        return NodeResult(next_node="act")

    def act(s: AgentGraphState) -> NodeResult:
        return NodeResult(next_node=END, outcome=GraphOutcome.ANSWERED)

    return GraphRuntime(nodes={"gate": gate, "act": act}, entry="gate", checkpointer=store)


def test_an_approval_cannot_resume_under_another_kernel_release(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.database import init_db

    init_db()
    store = InMemoryCheckpointStore()
    runtime = _approval_graph(store)
    thread_id = f"kernel-seal-{time.time_ns()}"
    state = AgentGraphState(
        query="Pay my penal tax", user_id="u1", tenant_id="t1",
        pending_action=TaxpayerActionRequest(action_type="generate_prn", description="PRN for UGX 200,000",
                                             payload={"amount": 200_000}),
    )
    paused = runtime.run(state, thread_id=thread_id)
    assert paused.outcome == GraphOutcome.WAITING_USER
    assert paused.kernel_version == release.current().version
    import app.agents.graphs.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_current_kernel_version", lambda: "9.9.9")
    with pytest.raises(PermissionError, match="kernel release changed"):
        runtime.resume(thread_id, {"approved": True, "action_digest": paused.pending_action.digest()},
                       expected_user_id="u1", expected_tenant_id="t1")


# --------------------------------------------------------------------------- contract


def test_the_contract_names_undeclared_steps() -> None:
    assert [v.step for v in undeclared_tools(["kernel_fact", "invent_a_refund"])] == ["invent_a_refund"]
    assert [v.step for v in undeclared_concepts(["vat_standard", "vat_holiday"])] == ["vat_holiday"]


def test_the_act_node_reports_an_undeclared_tool_as_such() -> None:
    from app.agents.graphs.main_graph import node_act

    state = AgentGraphState(query="Refund me", plan=["invent_a_refund"])
    node_act(state)
    assert {"name": "invent_a_refund", "reason": UNDECLARED_TOOL} in state.skipped_tools
    assert state.tool_calls == []


# --------------------------------------------------------------------------- precedence

VAT = {"key": "vat_standard", "display_name": "VAT standard rate", "value": 0.18, "unit": "ratio",
       "provision": "Value Added Tax Act"}


def test_a_lower_source_stating_another_figure_is_a_conflict() -> None:
    passage = {"text": "Under the old guide the VAT standard rate is 16% on taxable supplies.",
               "doc_type": "pdf_chunk", "source": "old-guide.pdf"}
    conflicts = precedence.find_conflicts([VAT], [passage])
    assert [(c.key, c.evidence_value, c.evidence_class) for c in conflicts] == [("vat_standard", "16%", "official_document")]


@pytest.mark.parametrize(
    "passage",
    [
        {"text": "The VAT standard rate is 18% on taxable supplies.", "doc_type": "pdf_chunk"},
        {"text": "Withholding tax on services is 6%.", "doc_type": "pdf_chunk"},
        {"text": "The VAT standard rate is 16%.", "doc_type": "graph"},
    ],
)
def test_agreement_unrelated_figures_and_equal_rank_are_not_conflicts(passage: dict) -> None:
    assert precedence.find_conflicts([VAT], [passage]) == []


def test_a_fused_graph_hit_records_its_conflicts(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list = []
    monkeypatch.setattr(precedence, "record_conflicts", lambda conflicts, kernel_version: recorded.extend(conflicts) or 1)
    graph_hit = {"doc_type": "graph", "graph_claims": [
        {"subject": functions.fact("vat_standard", _dt.date(2026, 8, 15)).display_name, "fiscal_year": "FY2026-27",
         "value": 0.18, "provision": "VAT Act"}]}
    name = graph_hit["graph_claims"][0]["subject"]
    hits = [graph_hit, {"text": f"The {name} is 16% this year.", "doc_type": "csv", "source": "faq.csv"}]
    conflicts = precedence.check_fused(graph_hit, hits)
    assert conflicts
    assert recorded == conflicts
    assert graph_hit["kernel_conflicts"][0]["evidence_class"] == "faq"


def test_precedence_order_is_fixed() -> None:
    order = sorted(precedence.SOURCE_RANK, key=precedence.SOURCE_RANK.get)
    assert order == ["computed", "kernel", "official_document", "faq", "live_web"]


def test_unused_edge_kinds_are_still_in_the_model() -> None:
    assert {r.kind for r in __import__("app.kernel.model", fromlist=["x"]).RELATIONSHIP_SPECS} >= set(EdgeKind)
