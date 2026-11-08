"""Fixes from the CodeRabbit review of PR 706 (ADR-0033), each pinned by a test.

Grouped by area: the review queue and audit, precedence, rules and tools, the
legal-change pipeline, the graph and Neo4j stores, the semantic layer, the
provenance export and the benchmark harness.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from app.graph import neo4j_store
from app.graph.schema import Node, NodeKind
from app.graph.store import InMemoryGraphStore
from app.kernel import functions, legal_change, precedence, refine, rules
from app.kernel.validate import check

ROOT = Path(__file__).resolve().parents[2]
AUG = _dt.date(2026, 8, 15)


# --------------------------------------------------------------------------- review queue and audit

OLD_DISCREPANCY_DDL = """CREATE TABLE knowledge_discrepancies (id TEXT PRIMARY KEY, conversation_id TEXT DEFAULT '',
 session_id TEXT DEFAULT '', user_id TEXT DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('pending','verified','dismissed','resolved')) DEFAULT 'pending',
 priority TEXT NOT NULL CHECK(priority IN ('low','normal','high','urgent')) DEFAULT 'normal',
 discrepancy_type TEXT NOT NULL CHECK(discrepancy_type IN ('outdated_law','incorrect_rate','hallucination',
   'procedure_changed','citation_mismatch','general')) DEFAULT 'outdated_law',
 bot_statement TEXT NOT NULL, user_correction TEXT NOT NULL, cited_sources_json TEXT DEFAULT '[]',
 frequency_count INTEGER NOT NULL DEFAULT 1, dedup_key TEXT DEFAULT '', admin_note TEXT DEFAULT '',
 verified_override_id TEXT DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL)"""


def test_an_old_sqlite_queue_is_rebuilt_to_accept_evidence_conflicts() -> None:
    from app import database

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(OLD_DISCREPANCY_DDL + "; INSERT INTO knowledge_discrepancies (id, bot_statement, "  # noqa: S608
                       "user_correction, created_at, updated_at) VALUES ('kept', 'a', 'b', 1, 1);")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO knowledge_discrepancies (id, discrepancy_type, bot_statement, user_correction, "
                     "created_at, updated_at) VALUES ('x', 'evidence_conflict', 'a', 'b', 1, 1)")
    database._refresh_discrepancy_type_check(conn)
    conn.execute("INSERT INTO knowledge_discrepancies (id, discrepancy_type, bot_statement, user_correction, "
                 "created_at, updated_at) VALUES ('x', 'evidence_conflict', 'a', 'b', 1, 1)")
    assert {r[0] for r in conn.execute("SELECT id FROM knowledge_discrepancies")} == {"kept", "x"}


def test_init_db_migrates_an_old_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import database

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(OLD_DISCREPANCY_DDL)
    monkeypatch.setattr(database, "_get_connection", lambda: conn)
    database.init_db()
    conn.execute("INSERT INTO knowledge_discrepancies (id, discrepancy_type, bot_statement, user_correction, "
                 "created_at, updated_at) VALUES ('x', 'evidence_conflict', 'a', 'b', 1, 1)")


def test_a_conflict_reaches_the_real_review_queue() -> None:
    from app import database

    database.init_db()
    conflict = precedence.Conflict(key="passenger_baggage_allowance_usd", display_name="Passenger baggage allowance",
                                   kernel_value="2000 USD", evidence_value="500 USD",
                                   evidence_source="review-fix-test.pdf", evidence_class="official_document",
                                   excerpt="allowance is US$500 (review fix test)", provision="EACCMA")
    assert precedence.record_conflicts([conflict], kernel_version="test") == 1
    rows = [r for r in database.list_discrepancies(limit=200) if "review fix test" in r["bot_statement"]]
    assert rows
    assert rows[0]["discrepancy_type"] == "evidence_conflict"


class _Cursor:
    def __init__(self, definition: str | None, fail: bool = False) -> None:
        self.definition, self.fail, self.sql = definition, fail, []

    def execute(self, sql: str, params=None) -> None:
        self.sql.append(sql)
        if self.fail and sql.startswith("ALTER TABLE"):
            raise RuntimeError("concurrent replica")

    def fetchone(self):
        return (self.definition,) if self.definition is not None else None


@pytest.mark.parametrize(("definition", "altered"), [
    ("CHECK ((discrepancy_type = ANY (ARRAY['outdated_law'::text, 'general'::text])))", True),
    (None, True),
    ("CHECK (discrepancy_type IN ('outdated_law', 'incorrect_rate', 'hallucination', 'procedure_changed', "
     "'citation_mismatch', 'general', 'evidence_conflict'))", False),
])
def test_postgres_adds_the_type_only_when_missing(definition, altered: bool) -> None:
    from app import postgres

    cur = _Cursor(definition)
    postgres._refresh_discrepancy_type_check(cur)
    assert any(s.startswith("ALTER TABLE") for s in cur.sql) is altered


def test_a_failed_postgres_alter_does_not_abort_the_migration() -> None:
    from app import postgres

    cur = _Cursor("CHECK (discrepancy_type IN ('general'))", fail=True)
    postgres._refresh_discrepancy_type_check(cur)
    assert cur.sql[-1] == "ROLLBACK TO SAVEPOINT discrepancy_type_check"


def test_the_rest_loops_tool_names_reach_the_ledger() -> None:
    from app.audit import turns

    assert [d["name"] for d in turns._tool_call_digests(["lookup_rate", {"name": "kernel_fact", "arguments": {"key": "x"}}])] \
        == ["lookup_rate", "kernel_fact"]


# --------------------------------------------------------------------------- precedence

VAT = {"key": "vat_standard", "display_name": "VAT standard rate", "value": 0.18, "unit": "ratio",
       "provision": "VAT Act", "fiscal_year": "FY2026-27"}


def test_a_passage_stating_the_figure_beside_another_rate_is_not_a_conflict() -> None:
    hit = {"text": "The VAT standard rate is 18%, while exports are zero-rated at 0%.", "doc_type": "pdf_chunk"}
    assert precedence.find_conflicts([VAT], [hit]) == []


def test_faq_promotion_cannot_put_a_contradicting_faq_first(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_record(*_a, **_k):
        raise AssertionError("ordering again must not queue the conflict twice")

    monkeypatch.setattr(precedence, "record_conflicts", must_not_record)
    monkeypatch.setattr(precedence, "facts_from_claims", lambda _claims: [VAT])
    stale = {"text": "The VAT standard rate is 17% on taxable supplies.", "doc_type": "csv", "id": "stale"}
    graph = {"doc_type": "graph", "graph_claims": [{"subject": "VAT standard rate"}], "id": "graph"}
    other = {"text": "Register for VAT on the URA portal.", "doc_type": "csv", "id": "other"}
    ordered = precedence.reorder_after_promotion([stale, graph, other])
    assert [h["id"] for h in ordered] == ["graph", "other", "stale"]
    assert precedence.reorder_after_promotion([stale, other]) == [stale, other], "no graph hit, no change"


def _baggage_graph_hit() -> dict:
    return {"doc_type": "graph", "text": "Passenger baggage duty-free allowance: USD 2,000",
            "graph_claims": [{"subject": "Passenger baggage duty-free allowance (USD)", "fiscal_year": "FY2026-27"}]}


def test_the_rest_fusion_path_ranks_a_contradicting_passage_last(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.service import ChatModel

    monkeypatch.setattr(precedence, "record_conflicts", lambda conflicts, **_: len(conflicts))
    monkeypatch.setattr(ChatModel, "_graph_hits", staticmethod(lambda _q: [_baggage_graph_hit()]))
    stale = {"text": "The passenger baggage duty-free allowance is US$500 per traveller.", "doc_type": "pdf_chunk",
             "id": "stale"}
    fine = {"text": "Declare goods to customs at Entebbe.", "doc_type": "pdf_chunk", "id": "fine"}
    fused, used = ChatModel._fuse_graph_leg("baggage allowance", [stale, fine])
    assert used
    assert [h.get("id") for h in fused if h.get("id")] == ["fine", "stale"]


def test_the_graph_fusion_path_ranks_a_contradicting_passage_last(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.graphs import main_graph
    from app.graph import shadow

    monkeypatch.setenv("FLAG_TAX_GRAPH", "true")
    monkeypatch.setenv("FLAG_GRAPH_FUSION", "true")
    monkeypatch.setattr(precedence, "record_conflicts", lambda conflicts, **_: len(conflicts))
    monkeypatch.setattr(shadow, "graph_hit_for", lambda _q: _baggage_graph_hit())
    stale = {"text": "The passenger baggage duty-free allowance is US$500 per traveller.", "doc_type": "pdf_chunk",
             "id": "stale"}
    fine = {"text": "Declare goods to customs at Entebbe.", "doc_type": "pdf_chunk", "id": "fine"}
    fused = main_graph._fuse_graph_leg("baggage allowance", [stale, fine])
    assert [h.get("id") for h in fused if h.get("id")] == ["fine", "stale"]


# --------------------------------------------------------------------------- rules and tools


@pytest.mark.parametrize("bad", ["nan", float("nan"), "inf", float("-inf")])
def test_a_non_finite_amount_is_an_input_error(bad) -> None:
    with pytest.raises(rules.RuleInputError):
        rules.late_filing_penal_tax(bad, 2, as_of=AUG)


def test_a_time_dependent_rule_needs_a_stated_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools import ToolRegistry

    monkeypatch.setenv("FLAG_TAX_GRAPH", "true")
    tool = ToolRegistry.get("kernel_compute_penalty")
    for rule in ("late_filing_penal_tax", "late_payment_interest"):
        assert "needs months" in tool.execute(rule=rule, amount=1_000_000, date="2026-08-15")["error"]
    assert tool.execute(rule="late_filing_penal_tax", amount=1_000_000, months=0, date="2026-08-15")["amount"] == 0.0
    assert tool.execute(rule="efris_penal_tax", amount=50_000, date="2026-08-15")["amount"] == 200_000.0


@pytest.mark.parametrize(("tool", "question", "expected"), [
    ("kernel_fact", "What is the stamp duty rate on transfer of land?", {"key": "stamp_duty_property_transfer"}),
    ("kernel_explain", "What does section 49(1) of the Tax Procedures Code impose?",
     {"provision": "prov:tax-procedures-code-act-cap-343-s-49-1"}),
    ("kernel_compute_penalty", "I filed my return late by 3 months with UGX 5,000,000 tax payable",
     {"rule": "late_filing_penal_tax", "amount": 5_000_000.0, "months": 3}),
    ("kernel_compute_penalty", "I filed my return late with UGX 5,000,000 tax payable", None),
    ("kernel_compute_penalty", "I failed to issue an EFRIS e-invoice on a sale with UGX 50,000 VAT",
     {"rule": "efris_penal_tax", "amount": 50_000.0}),
])
def test_the_graph_binds_kernel_tools_only_from_what_the_question_states(tool, question, expected) -> None:
    from app.agents.graphs.main_graph import _bind_kernel_tool

    assert _bind_kernel_tool(tool, question, "") == expected


def test_an_unverified_figure_keeps_its_warning_in_lookups_and_workflows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import calculator_router
    from app.tools import ToolRegistry
    from app.workflows import registry

    explanation = ToolRegistry.call("lookup_rate", {"tax_type": "motor_vehicle_duplicate_logbook_fee"})["explanation"]
    assert "not been confirmed" in explanation
    assert "official" not in explanation
    monkeypatch.setattr(calculator_router, "plan_rate_lookup",
                        lambda _q: calculator_router.RatePlan(tax_type="motor_vehicle_duplicate_logbook_fee"))
    reply = registry.resolve_workflow_side_question("How much is a duplicate logbook?")
    assert "not been confirmed" in reply


# --------------------------------------------------------------------------- legal change and refinement


def _draft(tmp_path: Path, value: float = 0.19) -> Path:
    data = json.loads((ROOT / "App/backend/app/tax/data/FY2026-27.json").read_text(encoding="utf-8"))
    data.update(fiscal_year="FY2027-28")
    data["rates"]["vat_standard"] = value
    path = tmp_path / "FY2027-28.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_stale_proposal_can_still_be_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNEL_REVIEW_DIR", str(tmp_path / "review"))
    draft = _draft(tmp_path)
    proposal = legal_change.propose(draft, [])
    draft.unlink()
    done = legal_change.decide(proposal["id"], approve=False, reviewer="A. Officer", role="Tax Policy",
                               reason="superseded by a new draft")
    assert done["status"] == legal_change.REJECTED


def test_proposing_the_same_draft_again_keeps_the_decision(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNEL_REVIEW_DIR", str(tmp_path / "review"))
    draft = _draft(tmp_path)
    first = legal_change.propose(draft, [])
    legal_change.decide(first["id"], approve=True, reviewer="A. Officer", role="Tax Policy")
    again = legal_change.propose(draft, [])
    assert again["status"] == legal_change.APPROVED
    assert again["decision"]["reviewer"] == "A. Officer"


def test_two_concurrent_decisions_cannot_both_succeed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNEL_REVIEW_DIR", str(tmp_path / "review"))
    proposal = legal_change.propose(_draft(tmp_path), [])
    outcomes: list[str] = []

    def decide(approve: bool) -> None:
        try:
            legal_change.decide(proposal["id"], approve=approve, reviewer="A. Officer", role="Tax Policy",
                                reason="" if approve else "no")
            outcomes.append("ok")
        except ValueError:
            outcomes.append("refused")

    threads = [threading.Thread(target=decide, args=(i % 2 == 0,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 1


def test_a_value_matching_a_better_named_rival_does_not_corroborate() -> None:
    candidates = legal_change.extract_candidates("The VAT refund limit on registration is UGX 5,000,000 a claim.")
    assert legal_change.corroborate("vat_registration_threshold_annual", 5_000_000, candidates,
                                    rivals=["vat_refund_limit_on_registration"]) == []
    assert legal_change.corroborate("vat_registration_threshold_annual", 5_000_000, candidates) != []


def test_an_uncited_key_counts_once_per_turn_across_years() -> None:
    trace = {"misses": [], "uncited_served": [{"key": "fee_x", "fiscal_year": "FY2025-26"},
                                              {"key": "fee_x", "fiscal_year": "FY2026-27"}]}
    rows = [{"event_type": "generate", "payload": {"kernel_trace": trace}}] * 2
    assert refine.suggestions(rows) == [], "two turns, not four"
    found = refine.suggestions(rows * 2)[0]
    assert (found["turns"], found["fiscal_years"]) == (4, ["FY2025-26", "FY2026-27"])


# --------------------------------------------------------------------------- graph and stores


def test_history_covers_every_fact_kind() -> None:
    from app.graph.query import effective_on, history

    answer = history("penal_tax_late_filing_monthly_rate", store=functions.kernel_store())
    assert answer.matched
    assert answer.claims[0].predicate == "penalty"
    assert effective_on("paye_due_date_monthly", AUG, store=functions.kernel_store()).matched


def test_a_memory_walk_never_exceeds_its_limit() -> None:
    store = functions.kernel_store()
    seeds = [n.id for n in store.by_kind(NodeKind.RATE)][:30]
    assert len(store.walk(seeds, hops=0, limit=10)) == 10, "more seeds than the limit"
    for limit in (1, 7, 50, 200):
        assert len(store.walk(["tax:vat"], hops=3, limit=limit)) <= limit


def test_replay_finds_a_key_stored_under_its_old_kind() -> None:
    raw = functions.kernel_store().to_dict()
    for node in raw["nodes"]:
        if node["id"] == "rate:vat_standard@FY2026-27":
            node["id"], node["kind"] = "threshold:vat_standard@FY2026-27", "threshold"
    past = InMemoryGraphStore.from_dict({"nodes": raw["nodes"], "edges": []})
    assert functions.fact("vat_standard", AUG, store=past, version="0.1.0").value == 0.18


def test_a_property_of_the_wrong_type_is_a_violation() -> None:
    store = InMemoryGraphStore()
    store.add_node(Node(id="tax:x", kind=NodeKind.TAX_TYPE, label="x", props={"name": 42}))
    assert "type" in {v.code for v in check(store, against_tables=False)}


def test_a_table_reload_refreshes_the_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tax import tables

    calls = []
    monkeypatch.setattr(functions, "refresh", lambda: calls.append("kernel"))
    tables.reload_tables()
    assert calls == ["kernel"]


@pytest.fixture
def fake():
    spec = importlib.util.spec_from_file_location("adv_fake", ROOT / "tests/agents/test_kernel_advanced_2026_11.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["adv_fake"] = module
    spec.loader.exec_module(module)
    return module.FakeNeo4j()


def test_neo4j_keeps_the_first_write_like_memory(fake) -> None:
    store = neo4j_store.Neo4jGraphStore(fake, release="r1")
    store.add_node(Node(id="tax:a", kind=NodeKind.TAX_TYPE, label="first", props={"name": "a"}))
    store.add_node(Node(id="tax:a", kind=NodeKind.TAX_TYPE, label="second", props={"name": "a"}))
    assert store.get("tax:a").label == "first"


def test_neo4j_releases_are_isolated_and_published_once(fake) -> None:
    built = functions.kernel_store()
    first = neo4j_store.Neo4jGraphStore(fake, release="r1")
    first.sync_from(built)
    merges = sum(1 for q, _ in fake.calls if q in neo4j_store.Q_MERGE_NODE.values())
    first.sync_from(built)
    assert sum(1 for q, _ in fake.calls if q in neo4j_store.Q_MERGE_NODE.values()) == merges, "published: not rewritten"
    assert first.published() == {"nodes": built.stats()["nodes"], "edges": built.stats()["edges"]}
    other = neo4j_store.Neo4jGraphStore(fake, release="r2")
    assert other.get("tax:vat") is None
    assert other.published() is None


def test_neo4j_seeds_respect_the_limit(fake) -> None:
    store = neo4j_store.Neo4jGraphStore(fake, release="r1")
    store.sync_from(functions.kernel_store())
    seeds = [n.id for n in functions.kernel_store().by_kind(NodeKind.RATE)][:30]
    assert len(store.walk(seeds, hops=0, limit=10)) == 10


def test_a_failed_neo4j_read_falls_back_to_memory(fake) -> None:
    built = functions.kernel_store()

    class Broken:
        def execute_query(self, *_a, **_k):
            raise ConnectionError("down")

        def close(self):
            pass

    wrapped = neo4j_store.FallbackGraphStore(neo4j_store.Neo4jGraphStore(Broken()), built)
    assert wrapped.get("tax:vat") is not None
    assert wrapped.active_name() == "memory (neo4j read failed)"


def test_a_failed_sync_closes_the_driver(monkeypatch: pytest.MonkeyPatch, fake) -> None:
    monkeypatch.setenv("GRAPH_STORE_BACKEND", "neo4j")

    def failing_sync(self, _store, **_k):
        raise ConnectionError("down")

    store = neo4j_store.Neo4jGraphStore(fake)
    monkeypatch.setattr(neo4j_store.Neo4jGraphStore, "from_env", classmethod(lambda cls, **_k: store))
    monkeypatch.setattr(neo4j_store.Neo4jGraphStore, "sync_from", failing_sync)
    built = functions.kernel_store()
    assert neo4j_store.select(built) is built
    assert fake.closed


def test_readiness_reports_the_store_actually_serving() -> None:
    import app.graph as graph
    from app.kernel.status import readiness_capabilities

    graph.reset_graph()
    assert graph.active_store_name() == "not loaded"
    assert readiness_capabilities()["graph_store_active"] == "not loaded"
    graph.get_graph()
    assert readiness_capabilities()["graph_store_active"] == "memory"


# --------------------------------------------------------------------------- semantic layer


def test_parameters_are_translated_outside_literals_only() -> None:
    from app.semantic_layer.adapter import to_pyformat

    sql = "SELECT x FROM t WHERE a = :p AND b LIKE 'VAT%' AND c = 'urn:sector' AND d::date = :d"
    assert to_pyformat(sql) == ("SELECT x FROM t WHERE a = %(p)s AND b LIKE 'VAT%%' AND c = 'urn:sector' "
                                "AND d::date = %(d)s")


def test_an_impossible_date_is_a_parameter_error(tmp_path: Path) -> None:
    from app.semantic_layer import catalogue

    metric = catalogue.Metric(id="m", title="", description="", owner="", sql="", sources=frozenset(),
                              parameters={"day": {"type": "date"}}, min_group_size=10)
    with pytest.raises(catalogue.MetricError, match="calendar date"):
        metric.bind({"day": "2026-02-31"})


def test_a_null_certifier_cannot_certify(tmp_path: Path) -> None:
    from app.semantic_layer import catalogue

    path = tmp_path / "m.json"
    path.write_text(json.dumps({"metrics": [{
        "id": "m", "sql": "SELECT a AS group_size FROM certified.t WHERE p = :p", "sources": ["certified.t"],
        "parameters": {"p": {"type": "code"}}, "status": "certified", "certified_by": None, "certified_at": None}]}))
    assert catalogue.certified(path) == {}


@pytest.mark.parametrize("sql", [
    "SELECT 1 AS group_size FROM certified.filing_compliance, taxpayers.registry",
    "SELECT 1 AS group_size FROM certified.filing_compliance AS c, taxpayers.registry r WHERE c.x = r.x",
    'SELECT "pg_read_file"(\'/etc/passwd\') AS group_size FROM certified.filing_compliance',
    'SELECT "dblink"(\'h\', \'q\') AS group_size FROM certified.filing_compliance',
])
def test_the_guard_reads_every_relation_and_quoted_name(sql: str) -> None:
    from app.semantic_layer import guard

    with pytest.raises(guard.SqlRejected):
        guard.check(sql, sources=frozenset({"certified.filing_compliance"}))


def test_metric_rows_are_json_and_connections_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from decimal import Decimal

    from app.semantic_layer import adapter

    assert json.dumps([adapter._jsonable(Decimal("0.81")), adapter._jsonable(_dt.date(2026, 7, 1))])
    seen = {}

    class FakePsycopg:
        @staticmethod
        def connect(dsn, **kwargs):
            seen.update(kwargs, dsn=dsn)
            raise ConnectionError("stop here")

    monkeypatch.setitem(sys.modules, "psycopg", FakePsycopg)
    monkeypatch.setattr(adapter, "vault_secret", lambda _path: {"dsn": "postgresql://reader@db/analytics"})
    monkeypatch.setenv("SEMANTIC_LAYER_VAULT_PATH", "kv/data/reader")
    monkeypatch.delenv("SEMANTIC_LAYER_DSN", raising=False)
    adapter.set_layer(None)
    try:
        layer = adapter.get_layer()
        with pytest.raises(ConnectionError):
            layer.executor.run("SELECT 1 AS group_size", {}, max_rows=1, timeout_s=1)
    finally:
        adapter.set_layer(None)
    assert seen["connect_timeout"] == adapter.CONNECT_TIMEOUT_S


# --------------------------------------------------------------------------- provenance, RDF and benchmark


def test_each_turn_keeps_its_own_date_and_the_agent_keeps_every_tool() -> None:
    from app.audit.provenance import export

    def row(event_id: str, as_of: str, tool: str) -> dict:
        return {"event_type": "generate", "event_id": event_id, "seq": 1, "ts": 1794000000.0, "user_id": "u",
                "payload": {"channel": "rest", "tool_calls": [{"name": tool}],
                            "kernel_pin": {"kernel_version": "1.2.2", "as_of": as_of}}}

    doc = export([row("a", "2026-08-01", "kernel_fact"), row("b", "2026-09-01", "lookup_rate")])
    dates = sorted(n["ura:asOf"] for n in doc["@graph"] if "prov:Activity" in n["@type"])
    assert dates == ["2026-08-01", "2026-09-01"]
    agent = next(n for n in doc["@graph"] if "agento:Agent" in n["@type"])
    assert agent["agento:usesTool"] == ["urn:ura-tool:kernel_fact", "urn:ura-tool:lookup_rate"]


def test_decimal_literals_have_no_exponent() -> None:
    from app.kernel.schema_export import _literal

    assert _literal(0.00001) == '"0.00001"^^xsd:decimal'


def test_the_kernel_runs_on_python_3_10_clocks() -> None:
    for name in ("kernel/legal_change.py", "kernel/refine.py", "kernel/release.py", "audit/provenance.py"):
        text = (ROOT / "App/backend/app" / name).read_text(encoding="utf-8")
        assert "_dt.UTC" not in text
        assert "import UTC" not in text, name


def _bench():
    spec = importlib.util.spec_from_file_location("bench_rf", ROOT / "scripts/bench_llm_concurrency.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_rf"] = module
    spec.loader.exec_module(module)
    return module


def test_p95_is_the_nearest_rank() -> None:
    bench = _bench()
    assert bench.percentile([1.0] * 19 + [10.0], 95) == 1.0
    assert bench.percentile([1.0] * 18 + [10.0, 10.0], 95) == 10.0


def test_a_zero_concurrency_level_is_refused() -> None:
    import asyncio

    bench = _bench()

    async def send(_prompt: str) -> None:
        return None

    with pytest.raises(ValueError, match="at least 1"):
        asyncio.run(bench.run_level(send, concurrency=0, requests=2))


def test_the_mda_resolver_uses_the_tables_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools import mda_interdependence

    class Table(dict):
        pass

    monkeypatch.setattr(mda_interdependence, "get_table", lambda *a, **k: Table(stamp_duty_property_transfer=0.02))
    tool = mda_interdependence.ResolveMdaNtrPaymentTool()
    result = tool.execute(service_type="mlhud_land_transfer", transaction_value_ugx=100_000_000)
    assert result["payable_ugx"] == 2_000_000.0


# --------------------------------------------------------------------------- second review batch


def test_the_semantic_layer_gate_sees_overrides_and_rollouts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.flags import flags
    from app.semantic_layer import config

    for name in ("FLAG_SEMANTIC_LAYER", "FLAG_SEMANTIC_LAYER_COHORTS", "FLAG_SEMANTIC_LAYER_PERCENT",
                 "FLAG_SEMANTIC_LAYER_ALLOWLIST", "VAULT_ADDR"):
        monkeypatch.delenv(name, raising=False)
    assert config.production_errors() == []
    monkeypatch.setenv("FLAG_SEMANTIC_LAYER_COHORTS", "ura_staff")
    assert any("G507" in e for e in config.production_errors()), "a cohort rollout is on"
    monkeypatch.delenv("FLAG_SEMANTIC_LAYER_COHORTS")
    flags.set("semantic_layer", True)
    try:
        assert any("G507" in e for e in config.production_errors()), "an admin override is on"
    finally:
        flags.clear("semantic_layer")


def test_the_tools_refuse_an_uncleared_layer_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.flags import flags
    from app.tools import ToolRegistry

    for name in ("VAULT_ADDR", "SEMANTIC_LAYER_VAULT_PATH", "SEMANTIC_LAYER_DSA_REF", "SEMANTIC_LAYER_DPIA_REF"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SEMANTIC_LAYER_DSN", "sqlite:///dev.db")
    flags.set("semantic_layer", True)  # turned on after start-up, past the start-up gate
    try:
        result = ToolRegistry.get("semantic_metric_catalogue").execute()
    finally:
        flags.clear("semantic_layer")
    assert result["ok"] is False
    assert "G507" in result["hint"]


def test_a_question_naming_a_year_is_answered_from_that_years_table() -> None:
    from app import calculator_router as cr
    from app.tax import tables

    reply, _ = cr.format_rate_reply(cr.RatePlan(tax_type="vat_registration_threshold_annual"),
                                    tables.get_table("FY2025-26"), requested_fiscal_year="FY2025-26")
    assert "150,000,000" in reply
    service = (ROOT / "App/backend/app/service.py").read_text(encoding="utf-8")
    assert "get_table(requested_fy) if requested_fy in list_fiscal_years()" in service


def test_a_named_year_gets_no_rollover_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import calculator_router as cr
    from app.tax import tables

    monkeypatch.setattr(tables, "kampala_today", lambda now=None: _dt.date(2027, 7, 2))
    plan = cr.RatePlan(tax_type="vat_standard")
    asked, _ = cr.format_rate_reply(plan, tables.get_table("FY2026-27"), requested_fiscal_year="FY2026-27")
    today, _ = cr.format_rate_reply(plan, tables.get_table())
    assert "No rate table has been published" not in asked
    assert "No rate table has been published" in today
    footer = cr._rate_table_footer({"fiscal_year": "FY2026-27", "period_requested": True})
    assert not any("No rate table has been published" in line for line in footer)


def test_no_literal_rate_stands_in_for_a_missing_entry() -> None:
    from app import calculator_router as cr
    from app.tax import tables

    reply, _ = cr.format_rate_reply(cr.RatePlan(tax_type="commercial_import_withholding_tax"), tables.get_table("FY2025-26"))
    assert "6%" not in reply


@pytest.mark.parametrize(("tool_name", "args"), [
    ("ResolveMdaNtrPaymentTool", {"service_type": "mlhud_land_transfer", "transaction_value_ugx": 100_000_000}),
])
def test_a_missing_stamp_duty_rate_is_an_error_not_zero(monkeypatch: pytest.MonkeyPatch, tool_name: str, args: dict) -> None:
    from app.tools import mda_interdependence

    monkeypatch.setattr(mda_interdependence, "get_table", lambda *a, **k: {})
    result = getattr(mda_interdependence, tool_name)().execute(**args)
    assert result["ok"] is False
    assert "stamp duty" in result["error"]
