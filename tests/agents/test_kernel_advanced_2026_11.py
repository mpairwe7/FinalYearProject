"""The Statutory Knowledge Kernel, phase 3 (ADR-0033).

The Neo4j backend behind ``GraphStore`` (parity with memory, fixed
parameterised Cypher, bounded walks, TLS in production, the threshold advisor),
the staff-only semantic layer (SELECT-only guard, certified aggregate metrics,
row and time caps, small-group suppression, the production gate), and offline
refinement from turn traces into the review queue.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

import pytest
from app.graph import neo4j_store
from app.graph.schema import Edge, EdgeKind, Node, NodeKind
from app.graph.store import MAX_HOPS, MAX_NODES
from app.kernel import functions, legal_change, refine, trace
from app.semantic_layer import adapter, catalogue, guard

AUG = _dt.date(2026, 8, 15)


# --------------------------------------------------------------------------- a Neo4j stand-in


class FakeNeo4j:
    """Interprets exactly the module's fixed statements, and records every call."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def execute_query(self, query: str, params: dict, **_kwargs):
        self.calls.append((query, params))
        q = neo4j_store
        if query in (q.Q_CONSTRAINT,):
            return [], None, []
        if query == q.Q_CLEAR:
            self.nodes.clear()
            self.edges.clear()
            return [], None, []
        if query in q.Q_MERGE_NODE.values():
            for row in params["rows"]:
                self.nodes[row["id"]] = dict(row)
            return [], None, []
        if query in q.Q_MERGE_EDGE.values():
            for row in params["rows"]:
                if row["src"] in self.nodes and row["dst"] in self.nodes:
                    self.edges[(row["src"], row["kind"], row["dst"])] = {"kind": row["kind"], "props_json": row["props_json"]}
            return [], None, []
        if query == q.Q_GET:
            node = self.nodes.get(params["id"])
            return ([{"n": node}] if node else []), None, []
        if query == q.Q_BY_KIND:
            return [{"n": n} for i, n in sorted(self.nodes.items()) if n["kind"] == params["kind"]], None, []
        if query in (q.Q_OUT, q.Q_IN):
            out = []
            for (src, kind, dst), rel in sorted(self.edges.items(), key=lambda e: (e[0][1], e[0][2], e[0][0])):
                if params["kinds"] and kind not in params["kinds"]:
                    continue
                if query == q.Q_OUT and src == params["id"]:
                    out.append({"r": rel, "src": src, "dst": dst, "b": self.nodes[dst]})
                if query == q.Q_IN and dst == params["id"]:
                    out.append({"r": rel, "src": src, "dst": dst, "b": self.nodes[src]})
            return sorted(out, key=lambda r: (r["r"]["kind"], r["b"]["id"])), None, []
        if query == q.Q_COUNT_NODES:
            counts: dict[str, int] = {}
            for n in self.nodes.values():
                counts[n["kind"]] = counts.get(n["kind"], 0) + 1
            return [{"kind": k, "n": v} for k, v in counts.items()], None, []
        if query == q.Q_COUNT_EDGES:
            return [{"n": len(self.edges)}], None, []
        raise AssertionError(f"unexpected statement: {query}")


@pytest.fixture
def synced():
    memory = functions.kernel_store()
    fake = FakeNeo4j()
    store = neo4j_store.Neo4jGraphStore(fake)
    store.sync_from(memory)
    return memory, store, fake


def _json(value):
    """As JSON sees it: a tuple and a list are the same value."""
    return json.loads(json.dumps(value, sort_keys=True))


def test_a_synced_neo4j_store_matches_memory(synced) -> None:
    memory, store, _fake = synced
    assert store.stats() == memory.stats()
    for kind in NodeKind:
        assert _json([n.to_dict() for n in store.by_kind(kind)]) == _json(sorted(
            (n.to_dict() for n in memory.by_kind(kind)), key=lambda d: d["id"]))
    for node_id in ("rate:vat_standard@FY2026-27", "tax:vat", "rate:paye_bands_resident@FY2026-27"):
        assert _json(store.get(node_id).to_dict()) == _json(memory.get(node_id).to_dict())
        for outgoing in (True, False):
            mine = sorted(json.dumps(_json([e.to_dict(), n.id])) for e, n in store.neighbours(node_id, outgoing=outgoing))
            theirs = sorted(json.dumps(_json([e.to_dict(), n.id])) for e, n in memory.neighbours(node_id, outgoing=outgoing))
            assert mine == theirs


def test_a_walk_returns_the_same_nodes_within_the_same_bounds(synced) -> None:
    memory, store, _fake = synced
    seeds = ["tax:vat"]
    walked = {n.id for n in store.walk(seeds, hops=2)}
    assert len(walked) > 20, "the walk must reach the VAT rates and their provisions"
    assert walked == {n.id for n in memory.walk(seeds, hops=2)}
    assert len(store.walk(seeds, hops=99, limit=10_000)) <= MAX_NODES
    every_tax = [n.id for n in memory.by_kind(NodeKind.TAX_TYPE)]
    assert len(store.walk(every_tax, hops=MAX_HOPS, limit=10_000)) == MAX_NODES, "the node bound must bind"
    deep = store.walk(seeds, hops=99, limit=MAX_NODES)
    assert {n.id for n in deep} == {n.id for n in memory.walk(seeds, hops=MAX_HOPS, limit=MAX_NODES)}


def test_only_fixed_statements_run_and_input_stays_in_parameters(synced) -> None:
    _memory, store, fake = synced
    hostile = "x'}) MATCH (n) DETACH DELETE n //"
    assert store.get(hostile) is None
    store.add_node(Node(id=hostile, kind=NodeKind.RATE, label=hostile, props={"key": hostile}))
    store.add_edge(Edge(src=hostile, kind=EdgeKind.RATED_FOR, dst="tax:vat"))
    assert all(query in neo4j_store.QUERIES for query, _ in fake.calls)
    assert all(hostile not in query for query, _ in fake.calls)
    with pytest.raises(neo4j_store.CypherNotAllowed):
        store._run(f"MATCH (n {{id: '{hostile}'}}) RETURN n")


def test_labels_and_relationship_types_come_only_from_the_enums() -> None:
    import re

    assert all(re.fullmatch(r"[A-Z][A-Za-z]+", label) for label in neo4j_store.NODE_LABELS.values())
    assert all(re.fullmatch(r"[A-Z_]+", rel) for rel in neo4j_store.REL_TYPES.values())


@pytest.mark.parametrize(
    ("env", "fragment"),
    [({"GRAPH_STORE_BACKEND": "neo4j", "NEO4J_URI": "bolt://db:7687", "NEO4J_USER": "u", "NEO4J_PASSWORD": "p"}, "TLS"),
     ({"GRAPH_STORE_BACKEND": "neo4j", "NEO4J_URI": "neo4j+ssc://db", "NEO4J_USER": "u", "NEO4J_PASSWORD": "p"}, "TLS"),
     ({"GRAPH_STORE_BACKEND": "neo4j", "NEO4J_URI": "neo4j+s://db"}, "NEO4J_USER"),
     ({"GRAPH_STORE_BACKEND": "arangodb"}, "not a known backend")],
)
def test_production_refuses_an_unsafe_graph_backend(monkeypatch, env: dict, fragment: str) -> None:
    for name in ("GRAPH_STORE_BACKEND", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    errors = neo4j_store.production_errors()
    assert any(fragment in e for e in errors)
    from app import production_readiness

    monkeypatch.setenv("APP_ENV", "production")
    assert any("G506" in e for e in production_readiness.gap_gate_errors())


def test_a_verified_tls_backend_with_credentials_passes(monkeypatch) -> None:
    monkeypatch.setenv("GRAPH_STORE_BACKEND", "neo4j")
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://graph.ura.internal:7687")
    monkeypatch.setenv("NEO4J_USER", "kernel_reader")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    assert neo4j_store.production_errors() == []
    monkeypatch.setenv("GRAPH_STORE_BACKEND", "memory")
    assert neo4j_store.production_errors() == []


def test_an_unreachable_neo4j_falls_back_to_memory(monkeypatch) -> None:
    monkeypatch.setenv("GRAPH_STORE_BACKEND", "neo4j")

    def unreachable():
        raise ConnectionError("no route to host")

    monkeypatch.setattr(neo4j_store.Neo4jGraphStore, "from_env", staticmethod(unreachable))
    built = functions.kernel_store()
    assert neo4j_store.select(built) is built


def test_the_advisor_applies_the_adr_thresholds(monkeypatch) -> None:
    store = functions.kernel_store()
    small = neo4j_store.advise(store, samples=50)
    assert (small.recommended, small.nodes) == (False, store.stats()["nodes"])
    assert neo4j_store.advise(store, samples=10, adhoc_cypher_needed=True).recommended
    monkeypatch.setattr(neo4j_store, "NODE_THRESHOLD", 100)
    assert "nodes exceeds" in neo4j_store.advise(store, samples=10).reasons[0]


# --------------------------------------------------------------------------- semantic layer: guard

SOURCES = frozenset({"certified.filing_compliance"})


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM certified.filing_compliance",
     "SELECT 1 AS group_size FROM certified.filing_compliance; DROP TABLE certified.filing_compliance",
     "SELECT * FROM certified.filing_compliance -- trailing comment",
     "SELECT * FROM certified.filing_compliance /* c */",
     "SELECT * INTO stolen FROM certified.filing_compliance",
     "SELECT pg_read_file('/etc/passwd') FROM certified.filing_compliance",
     "SELECT dblink('host=evil', 'select 1') AS group_size FROM certified.filing_compliance",
     "SELECT set_config('role', 'admin', false) AS group_size FROM certified.filing_compliance",
     "SELECT current_setting('app.secret') AS group_size FROM certified.filing_compliance",
     "SELECT 1 AS group_size FROM certified.filing_compliance; SELECT 2 AS group_size FROM certified.filing_compliance",
     "SELECT * FROM taxpayers.registry",
     "SELECT * FROM certified.filing_compliance JOIN taxpayers.registry ON true",
     "SELECT * FROM certified.filing_compliance WHERE period = ?",
     "SELECT $$x$$ FROM certified.filing_compliance",
     "SELECT * FROM certified.filing_compliance FOR UPDATE",
     "WITH x AS (DELETE FROM certified.filing_compliance RETURNING *) SELECT * FROM x",
     "SELECT * FROM pg_catalog.pg_user",
     "SELECT 'unterminated FROM certified.filing_compliance",
     "UPDATE certified.filing_compliance SET x = 1"],
)
def test_the_guard_refuses_anything_but_one_select_over_declared_sources(sql: str) -> None:
    with pytest.raises(guard.SqlRejected):
        guard.check(sql, sources=SOURCES)


def test_the_guard_accepts_a_template_and_reports_its_parameters() -> None:
    sql = ("SELECT tax_head, 'delete' AS label, count(*) AS group_size FROM certified.filing_compliance "
           "WHERE period BETWEEN :period_from AND :period_to AND x::date IS NOT NULL GROUP BY tax_head")
    assert guard.check(sql, sources=SOURCES) == {"period_from", "period_to"}


# --------------------------------------------------------------------------- semantic layer: catalogue and adapter


def test_the_shipped_catalogue_is_valid_and_certifies_nothing() -> None:
    metrics = catalogue.load()
    assert metrics, "the catalogue documents the intended metrics"
    assert catalogue.certified() == {}


def _catalogue(tmp_path: Path, **overrides) -> Path:
    metric = {
        "id": "filing_on_time_rate", "title": "t", "description": "d", "owner": "Data Office",
        "sql": "SELECT tax_head, ratio, returns_due AS group_size FROM filing_compliance "
               "WHERE period = :period ORDER BY tax_head",
        "sources": ["filing_compliance"], "parameters": {"period": {"type": "period"}}, "min_group_size": 10,
        "status": "certified", "certified_by": "A. Owner", "certified_at": "2026-11-08",
    }
    metric.update(overrides)
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps({"metrics": [metric]}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("overrides", "match"),
    [({"parameters": {}}, "binds"), ({"min_group_size": 3}, "at least"),
     ({"sql": "SELECT tax_head FROM filing_compliance WHERE period = :period"}, "group_size"),
     ({"sql": "SELECT returns_due AS group_size FROM secrets WHERE period = :period"}, "undeclared")],
)
def test_a_malformed_metric_fails_the_load(tmp_path: Path, overrides: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        catalogue.load(_catalogue(tmp_path, **overrides))


def test_certification_needs_a_named_owner_and_date(tmp_path: Path) -> None:
    assert catalogue.certified(_catalogue(tmp_path, certified_by=""))  == {}
    assert catalogue.certified(_catalogue(tmp_path, status="draft")) == {}
    assert set(catalogue.certified(_catalogue(tmp_path))) == {"filing_on_time_rate"}


@pytest.fixture
def layer(tmp_path: Path):
    db = tmp_path / "analytics.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE filing_compliance (tax_head TEXT, period TEXT, ratio REAL, returns_due INTEGER)")
    conn.executemany("INSERT INTO filing_compliance VALUES (?, ?, ?, ?)", [
        ("vat", "2026-07", 0.81, 4200), ("paye", "2026-07", 0.93, 9100), ("excise", "2026-07", 1.0, 3),
        ("income_tax", "2026-07", 0.64, 2500), ("vat", "2026-06", 0.79, 4100)])
    conn.commit()
    conn.close()
    built = adapter.SemanticLayer(adapter.SqliteExecutor(str(db)), catalogue.certified(_catalogue(tmp_path)))
    adapter.set_layer(built)
    yield built
    adapter.set_layer(None)


def test_a_certified_metric_runs_and_small_groups_are_suppressed(layer) -> None:
    result = layer.query("filing_on_time_rate", {"period": "2026-07"})
    assert [r["tax_head"] for r in result["rows"]] == ["income_tax", "paye", "vat"]
    assert result["suppressed_rows"] == 1, "a group of 3 returns names too few taxpayers"
    assert result["truncated"] is False


def test_results_are_capped(layer) -> None:
    layer.max_rows = 2
    result = layer.query("filing_on_time_rate", {"period": "2026-07"})
    assert result["truncated"] is True
    assert len(result["rows"]) + result["suppressed_rows"] == 2


@pytest.mark.parametrize(("metric", "params", "match"),
                         [("not_a_metric", {}, "not a certified"), ("filing_on_time_rate", {}, "needs period"),
                          ("filing_on_time_rate", {"period": "July"}, "must be a period"),
                          ("filing_on_time_rate", {"period": "2026-07", "tin": "1000"}, "unknown parameters")])
def test_a_query_refuses_unknown_metrics_and_bad_parameters(layer, metric: str, params: dict, match: str) -> None:
    with pytest.raises(catalogue.MetricError, match=match):
        layer.query(metric, params)


def test_the_executor_is_read_only_and_time_bounded(monkeypatch, tmp_path: Path, layer) -> None:
    db = tmp_path / "analytics.db"
    opened: list[str] = []
    real_connect = sqlite3.connect

    def recording_connect(target, *args, **kwargs):
        opened.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(adapter.sqlite3, "connect", recording_connect)
    layer.query("filing_on_time_rate", {"period": "2026-07"})
    assert opened
    assert all(o.endswith("?mode=ro") for o in opened)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        real_connect(opened[0], uri=True).execute("DELETE FROM filing_compliance")
    slow = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 50000000) "
            "SELECT count(*) AS group_size FROM c")
    with pytest.raises(sqlite3.OperationalError, match="interrupt"):
        adapter.SqliteExecutor(str(db)).run(slow, {}, max_rows=10, timeout_s=0.05)


def test_the_tools_are_staff_only_and_gated(monkeypatch, layer) -> None:
    from app.mcp.policy import authorize_tool_call
    from app.tools import ToolRegistry

    query = ToolRegistry.get("semantic_metric_query")
    listing = ToolRegistry.get("semantic_metric_catalogue")
    for tool in (query, listing):
        schema = tool.schema
        assert (schema.namespace, schema.risk, schema.allowed_roles) == ("semantic_layer", "high", ("ura_staff", "ura_admin"))
        assert schema.required_scopes == ("analytics",)
        for role, allowed in (("taxpayer", False), ("verified_taxpayer", False), ("ura_staff", True)):
            decision = authorize_tool_call(name=schema.name, risk=schema.risk, user_role=role, user_id="u-1",
                                           allowed_roles=schema.allowed_roles, required_scopes=schema.required_scopes,
                                           scope_exempt_roles=schema.scope_exempt_roles, record_velocity=False)
            assert decision["allowed"] is allowed, role
    monkeypatch.delenv("FLAG_SEMANTIC_LAYER", raising=False)
    assert query.execute(metric="filing_on_time_rate", parameters={"period": "2026-07"})["ok"] is False
    monkeypatch.setenv("FLAG_SEMANTIC_LAYER", "true")
    assert listing.execute()["metrics"][0]["id"] == "filing_on_time_rate"
    assert len(query.execute(metric="filing_on_time_rate", parameters={"period": "2026-07"})["rows"]) == 3
    assert "certified" in query.execute(metric="draft_metric")["error"]


def test_the_tool_reports_a_timeout_without_detail(monkeypatch, layer) -> None:
    from app.tools import ToolRegistry

    def stuck(*_a, **_k):
        raise sqlite3.OperationalError("interrupted")

    monkeypatch.setenv("FLAG_SEMANTIC_LAYER", "true")
    monkeypatch.setattr(layer.executor, "run", stuck)
    result = ToolRegistry.get("semantic_metric_query").execute(metric="filing_on_time_rate", parameters={"period": "2026-07"})
    assert result == {"ok": False, "error": "The metric could not be run (time limit or database error)."}


def test_production_refuses_the_semantic_layer_without_its_conditions(monkeypatch, tmp_path: Path) -> None:
    from app import production_readiness
    from app.semantic_layer import config

    for name in ("VAULT_ADDR", "SEMANTIC_LAYER_VAULT_PATH", "SEMANTIC_LAYER_DSN", "SEMANTIC_LAYER_DSA_REF",
                 "SEMANTIC_LAYER_DPIA_REF", "SEMANTIC_LAYER_CATALOGUE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("FLAG_SEMANTIC_LAYER", raising=False)
    assert config.production_errors() == []
    monkeypatch.setenv("FLAG_SEMANTIC_LAYER", "true")
    errors = " ".join(config.production_errors())
    for needle in ("VAULT_ADDR", "SEMANTIC_LAYER_VAULT_PATH", "SEMANTIC_LAYER_DSA_REF", "SEMANTIC_LAYER_DPIA_REF",
                   "no metric is certified"):
        assert needle in errors
    monkeypatch.setenv("VAULT_ADDR", "https://vault.ura.internal")
    monkeypatch.setenv("SEMANTIC_LAYER_VAULT_PATH", "kv/data/semantic-layer/reader")
    monkeypatch.setenv("SEMANTIC_LAYER_DSA_REF", "URA-DSA-2026-014")
    monkeypatch.setenv("SEMANTIC_LAYER_DPIA_REF", "URA-DPIA-2026-031")
    monkeypatch.setenv("SEMANTIC_LAYER_CATALOGUE", str(_catalogue(tmp_path)))
    assert config.production_errors() == []
    monkeypatch.setenv("SEMANTIC_LAYER_DSN", "postgresql://reader:pw@db/analytics")
    assert any("must not be set" in e for e in config.production_errors())
    monkeypatch.setenv("APP_ENV", "production")
    assert any("G507" in e for e in production_readiness.gap_gate_errors())


# --------------------------------------------------------------------------- refinement


def test_a_turn_trace_records_misses_and_uncited_figures_by_key_only() -> None:
    token = trace.start()
    try:
        functions.fact("carbon_border_levy", AUG)
        functions.fact("What is VAT?", AUG)
        functions.fact("motor_vehicle_duplicate_logbook_fee", AUG)
        functions.fact("vat_standard", AUG)
        functions.fact("carbon_border_levy", AUG)
        snap = trace.snapshot()
    finally:
        trace.TURN_KERNEL_TRACE.reset(token)
    assert snap["misses"] == [{"concept": "carbon_border_levy", "reason": "undeclared"}]
    assert snap["uncited_served"] == [{"key": "motor_vehicle_duplicate_logbook_fee", "fiscal_year": "FY2026-27"}]
    assert trace.snapshot() is None, "no trace outside a turn"


def test_a_replay_is_not_traced() -> None:
    token = trace.start()
    try:
        functions.fact("carbon_border_levy", AUG, store=functions.kernel_store(), version="1.1.0")
        assert trace.snapshot() is None
    finally:
        trace.TURN_KERNEL_TRACE.reset(token)


def test_the_audit_row_carries_the_trace() -> None:
    from app.audit import turns

    token = trace.start()
    try:
        functions.fact("carbon_border_levy", AUG)
        payload = turns.build_turn_payload(message="m", result={"reply": "r"}, channel="rest")
    finally:
        trace.TURN_KERNEL_TRACE.reset(token)
    assert payload["schema"] == turns.TURN_PAYLOAD_SCHEMA == 5
    assert payload["kernel_trace"]["misses"][0]["concept"] == "carbon_border_levy"


def test_every_turn_opens_a_trace() -> None:
    from app.service import _scoped_conversation_state

    class Probe:
        @_scoped_conversation_state
        def answer(self, message: str, conversation_id: str | None = None, session_id: str | None = None,
                   user_id: str | None = None, tenant_id: str | None = None):
            functions.fact("carbon_border_levy", AUG)
            return trace.snapshot()

    assert Probe().answer("q", session_id="s-1")["misses"][0]["concept"] == "carbon_border_levy"
    assert trace.snapshot() is None


def _rows(*traces) -> list[dict]:
    return [{"event_type": "generate", "payload": {"kernel_trace": t}} for t in traces]


MISS = {"misses": [{"concept": "carbon_border_levy", "reason": "undeclared"}], "uncited_served": []}
UNCITED = {"misses": [], "uncited_served": [{"key": "paye_due_date_monthly", "fiscal_year": "FY2026-27"}]}


def test_only_what_recurs_across_turns_is_suggested() -> None:
    assert refine.suggestions(_rows(MISS, MISS)) == []
    doubled = {"misses": MISS["misses"] * 2, "uncited_served": []}
    assert refine.suggestions(_rows(doubled, MISS)) == [], "a repeat within one turn counts once"
    found = refine.suggestions(_rows(MISS, MISS, MISS, UNCITED, UNCITED, UNCITED, UNCITED),
                               [{"discrepancy_type": "evidence_conflict", "frequency_count": 5, "id": "d1",
                                 "bot_statement": "VAT (standard rate): 18%"},
                                {"discrepancy_type": "outdated_law", "frequency_count": 9, "id": "d2"}])
    assert [(s["kind"], s["turns"]) for s in found] == [("evidence_conflict", 5), ("uncited_figure", 4),
                                                         ("kernel_miss", 3)]


def test_a_refinement_is_queued_for_a_named_reviewer(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNEL_REVIEW_DIR", str(tmp_path))
    proposal = refine.propose(refine.suggestions(_rows(MISS, MISS, MISS)))
    assert proposal["status"] == legal_change.PENDING
    assert proposal["type"] == "refinement"
    assert [p["id"] for p in legal_change.list_proposals()] == [proposal["id"]]
    done = legal_change.decide(proposal["id"], approve=False, reviewer="A. Officer", role="Tax Policy",
                               reason="Not a Ugandan tax")
    assert done["status"] == legal_change.REJECTED
    assert refine.propose([]) is None


def test_a_proposal_whose_draft_is_gone_cannot_be_approved(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNEL_REVIEW_DIR", str(tmp_path / "review"))
    draft = tmp_path / "FY2027-28.json"
    draft.write_text(Path(functions.__file__).with_name("..").joinpath("tax/data/FY2026-27.json").resolve()
                     .read_text(encoding="utf-8"), encoding="utf-8")
    proposal = legal_change.propose(draft, [])
    draft.unlink()
    with pytest.raises(ValueError, match="missing"):
        legal_change.decide(proposal["id"], approve=True, reviewer="A. Officer", role="Tax Policy")


# --------------------------------------------------------------------------- consistency with the calculator


@pytest.mark.parametrize(("payable", "months"), [(1_000_000, 1), (5_000_000, 3), (20_000_000, 7), (300_000, 60)])
def test_the_kernel_rules_agree_with_the_late_filing_calculator(payable: int, months: int) -> None:
    from app.kernel import rules
    from app.tools import ToolRegistry

    calc = ToolRegistry.get("penalty_late_filing").execute(tax_payable_ugx=payable, months_late=months, date="2026-08-15")
    data = calc.get("result", calc)
    penal = rules.late_filing_penal_tax(payable, months, as_of=AUG)
    interest = rules.late_payment_interest(payable, months, penal_tax=penal.amount, as_of=AUG)
    assert penal.amount == data["penal_tax"]
    assert interest.amount == data["capped_interest_ugx"]
