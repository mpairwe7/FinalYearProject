"""The Statutory Knowledge Kernel, phase 2 (ADR-0033).

Rules as code for penalties and interest, golden multi-step scenarios with
asserted traces (fiscal-year boundary, contradicting evidence, replay from a
release snapshot), the legal-change pipeline and its review queue, the
PROV-O / AgentO provenance export and the model benchmark harness.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from app.kernel import functions, legal_change, precedence, release, rules, scenarios

ROOT = Path(__file__).resolve().parents[2]
AUG = _dt.date(2026, 8, 15)


# --------------------------------------------------------------------------- rules


@pytest.mark.parametrize(
    ("payable", "months", "expected"),
    [(5_000_000, 3, 300_000.0), (1_000_000, 2, 200_000.0), (20_000_000, 3, 1_200_000.0), (5_000_000, 0, 0.0)],
)
def test_late_filing_penal_tax_is_the_greater_of_minimum_and_rate(payable: int, months: int, expected: float) -> None:
    result = rules.late_filing_penal_tax(payable, months, as_of=AUG)
    assert result.amount == expected
    assert all("s.49(1)" in step.provision for step in result.steps)


@pytest.mark.parametrize(
    ("due", "filed", "months"),
    [((2026, 7, 15), (2026, 7, 15), 0), ((2026, 7, 15), (2026, 7, 16), 1), ((2026, 7, 15), (2026, 9, 15), 2),
     ((2026, 7, 15), (2026, 9, 16), 3), ((2026, 12, 31), (2027, 2, 1), 2)],
)
def test_a_part_month_of_late_filing_counts_as_a_month(due: tuple, filed: tuple, months: int) -> None:
    assert rules.late_filing_months(_dt.date(*due), _dt.date(*filed)) == months


def test_interest_is_simple_and_capped_at_principal_plus_penal_tax() -> None:
    plain = rules.late_payment_interest(1_000_000, 6, as_of=AUG)
    assert plain.amount == 120_000.0
    capped = rules.late_payment_interest(1_000_000, 80, penal_tax=200_000, as_of=AUG)
    assert capped.amount == 1_200_000.0
    assert "s.136(5) and (7)" in capped.steps[1].provision
    assert capped.notes, "the part-month convention it does not invent must be stated"


@pytest.mark.parametrize(("rule", "due", "expected"),
                         [(rules.efris_penal_tax, 50_000, 200_000.0), (rules.efris_penal_tax, 500_000, 1_000_000.0),
                          (rules.dts_unstamped_penal_tax, 300_000, 2_000_000.0),
                          (rules.dts_unstamped_penal_tax, 1_500_000, 3_000_000.0)])
def test_penal_taxes_are_double_the_tax_or_the_minimum(rule, due: int, expected: float) -> None:
    assert rule(due, as_of=AUG).amount == expected


def test_a_rule_before_its_provision_existed_is_not_in_the_kernel() -> None:
    result = rules.efris_penal_tax(50_000, as_of=_dt.date(2026, 3, 1))
    assert isinstance(result, functions.NotInKernel)
    assert result.reason == "not_in_year"


@pytest.mark.parametrize(("call", "args"), [(rules.late_filing_penal_tax, (-1, 2)), (rules.late_filing_penal_tax, (100, 1.5)),
                                            (rules.late_payment_interest, (100, -2)), (rules.efris_penal_tax, ("abc",))])
def test_rules_refuse_inputs_they_cannot_apply(call, args: tuple) -> None:
    with pytest.raises(rules.RuleInputError):
        call(*args, as_of=AUG)


def test_every_rule_is_unverified_until_the_tax_team_approves_it() -> None:
    for name, fn in rules.RULES.items():
        args = (1_000_000, 2) if "late" in name else (1_000_000,)
        result = fn(*args, as_of=AUG)
        assert result.review_status == rules.REVIEW_PENDING, name
        assert result.unverified, name


def test_marking_a_rule_approved_needs_an_approved_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(rules.REVIEW_STATUS, "efris_penal_tax", rules.REVIEW_APPROVED)
    unreviewed = rules.efris_penal_tax(50_000, as_of=AUG)
    assert (unreviewed.review_status, unreviewed.unverified) == (rules.REVIEW_AWAITING_RELEASE, True)
    approved = release.KernelVersion(**{**release.current().__dict__, "review_status": release.REVIEW_APPROVED})
    monkeypatch.setattr(release, "current", lambda: approved)
    reviewed = rules.efris_penal_tax(50_000, as_of=AUG)
    assert (reviewed.review_status, reviewed.unverified) == (rules.REVIEW_APPROVED, False)


# --------------------------------------------------------------------------- scenarios


@pytest.mark.parametrize("scenario", scenarios.GOLDEN, ids=lambda s: s.id)
def test_golden_scenarios_answer_and_trace(scenario: scenarios.Scenario) -> None:
    trace = scenarios.run(scenario)
    assert scenarios.check(scenario, trace) == []
    assert trace.kernel_version == release.current().version


def test_the_fiscal_year_boundary_changes_the_answer() -> None:
    by_id = {s.id: s for s in scenarios.GOLDEN}
    june = scenarios.run(by_id["vat-threshold-30-june"]).calls[0]
    july = scenarios.run(by_id["vat-threshold-1-july"]).calls[0]
    assert (june["fiscal_year"], july["fiscal_year"]) == ("FY2025-26", "FY2026-27")
    assert june["value"] != july["value"]


def test_contradicting_evidence_is_recorded_and_the_figure_stands() -> None:
    scenario = next(s for s in scenarios.GOLDEN if s.id == "late-filing-penal-tax")
    minimum = functions.fact("penal_tax_late_filing_minimum_ugx", AUG)
    passage = {"text": f"Old notice: the {minimum.display_name} is UGX 150,000 per return.", "doc_type": "pdf_chunk",
               "source": "notice-2019.pdf"}
    fact_dict = {"key": minimum.key, "display_name": minimum.display_name, "value": minimum.value, "unit": minimum.unit,
                 "provision": minimum.provision}
    conflicts = precedence.find_conflicts([fact_dict], [passage])
    assert [c.evidence_value for c in conflicts] == ["150000 UGX"]
    assert scenarios.check(scenario, scenarios.run(scenario)) == []


def test_every_release_has_a_snapshot_matching_its_hash() -> None:
    for entry in release.load_registry():
        store = release.load_snapshot(entry["version"])
        assert release.content_sha256(store) == entry["content_sha256"]


@pytest.mark.parametrize("version", [e["version"] for e in release.load_registry()])
def test_a_recorded_release_replays_to_identical_facts(version: str) -> None:
    store = release.load_snapshot(version)
    for scenario in scenarios.GOLDEN:
        if any(step.call != "fact" for step in scenario.steps):
            continue
        replayed = scenarios.run(scenario, store=store, version=version)
        live = scenarios.run(scenario)
        assert [c["value"] for c in replayed.calls] == [c["value"] for c in live.calls]
        assert replayed.kernel_version == version


def test_a_tampered_snapshot_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import gzip

    entry = release.load_registry()[0]
    real = release.snapshot_path(entry["content_sha256"])
    raw = json.loads(gzip.decompress(real.read_bytes()))
    node = next(n for n in raw["nodes"] if n["id"] == "rate:vat_standard@FY2026-27")
    node["props"]["value"] = 0.99
    monkeypatch.setattr(release, "SNAPSHOT_DIR", tmp_path)
    release.snapshot_path(entry["content_sha256"]).write_bytes(gzip.compress(json.dumps(raw).encode()))
    with pytest.raises(ValueError, match="does not match"):
        release.load_snapshot(entry["version"])


def test_replay_reads_the_snapshot_not_the_running_kernel() -> None:
    from app.graph.store import InMemoryGraphStore

    raw = functions.kernel_store().to_dict()
    node = next(n for n in raw["nodes"] if n["id"] == "rate:vat_standard@FY2026-27")
    node["props"]["value"] = 0.99
    past = InMemoryGraphStore.from_dict(raw)
    replayed = functions.fact("vat_standard", AUG, store=past, version="0.9.0")
    assert (replayed.value, replayed.kernel_version) == (0.99, "0.9.0")
    assert functions.fact("vat_standard", AUG).value == 0.18


def test_a_ruleset_change_needs_a_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "rules_sha256", lambda: "f" * 64)
    assert not release.compute().released


# --------------------------------------------------------------------------- legal change

ACT = ("THE VALUE ADDED TAX (AMENDMENT) ACT, 2027. 3. The standard rate of value added tax is nineteen per cent, "
       "that is 19%. 4. Late filing: a person who fails to furnish a return pays the greater of UGX 250,000 or two "
       "per cent of the tax.")


def _draft(tmp_path: Path) -> Path:
    data = json.loads((ROOT / "App/backend/app/tax/data/FY2026-27.json").read_text(encoding="utf-8"))
    data.update(fiscal_year="FY2027-28", effective_from="2027-07-01", effective_to="2028-06-30")
    data["rates"].update(vat_standard=0.19, penal_tax_late_filing_minimum_ugx=250000, objection_timeline_days=30)
    path = tmp_path / "FY2027-28.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_candidates_are_extracted_with_their_sentences() -> None:
    found = {(c.kind, c.value) for c in legal_change.extract_candidates(ACT, source="act.txt")}
    assert {("percent", 19.0), ("amount_ugx", 250000.0)} <= found


def test_a_draft_is_diffed_and_each_change_checked_against_the_act(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNEL_REVIEW_DIR", str(tmp_path / "review"))
    act = tmp_path / "act.txt"
    act.write_text(ACT, encoding="utf-8")
    proposal = legal_change.propose(_draft(tmp_path), [act])
    changes = {c["key"]: c["corroborated"] for c in proposal["changes"]}
    assert changes == {"vat_standard": True, "penal_tax_late_filing_minimum_ugx": True, "objection_timeline_days": False}
    assert proposal["status"] == legal_change.PENDING


def test_a_value_alone_does_not_corroborate_a_figure() -> None:
    unrelated = legal_change.extract_candidates("An appeal to the Tribunal lies within 30 days of the decision.")
    related = legal_change.extract_candidates("A notice of objection shall be lodged within 30 days of the assessment.")
    assert legal_change.corroborate("objection_timeline_days", 30, unrelated) == []
    assert len(legal_change.corroborate("objection_timeline_days", 30, related)) == 1


def test_only_a_named_reviewer_decides_and_only_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNEL_REVIEW_DIR", str(tmp_path / "review"))
    act = tmp_path / "act.txt"
    act.write_text(ACT, encoding="utf-8")
    proposal = legal_change.propose(_draft(tmp_path), [act])
    with pytest.raises(ValueError, match="name and role"):
        legal_change.decide(proposal["id"], approve=True, reviewer="", role="")
    with pytest.raises(ValueError, match="reason"):
        legal_change.decide(proposal["id"], approve=False, reviewer="A. Officer", role="Tax Policy")
    done = legal_change.decide(proposal["id"], approve=True, reviewer="A. Officer", role="Commissioner, Tax Policy")
    assert done["status"] == legal_change.APPROVED
    with pytest.raises(ValueError, match="already approved"):
        legal_change.decide(proposal["id"], approve=True, reviewer="A. Officer", role="Commissioner, Tax Policy")


def test_a_draft_edited_after_proposal_cannot_be_approved(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNEL_REVIEW_DIR", str(tmp_path / "review"))
    draft = _draft(tmp_path)
    proposal = legal_change.propose(draft, [])
    draft.write_text(draft.read_text(encoding="utf-8").replace("0.19", "0.2"), encoding="utf-8")
    with pytest.raises(ValueError, match="changed after"):
        legal_change.decide(proposal["id"], approve=True, reviewer="A. Officer", role="Tax Policy")


# --------------------------------------------------------------------------- provenance


def _row(**payload) -> dict:
    return {"event_type": "generate", "event_id": "e1", "seq": 3, "ts": 1794000000.0, "user_id": "taxpayer-42",
            "row_hash": "r", "payload": {"channel": "rest", "model": "Sunbird/Sunflower-Qwen3.5-9B",
                                         "reply_sha256": "a" * 64, "tool_calls": [{"name": "kernel_fact"}],
                                         "kernel_pin": {"kernel_version": "1.1.0", "ruleset_version": "1.0.0",
                                                        "content_sha256": "4000", "as_of": "2026-08-15"},
                                         **payload}}


def test_a_turn_exports_as_prov_and_agento() -> None:
    from app.audit.provenance import export

    doc = export([_row(), {"event_type": "tool_call", "payload": {}}])
    types = {t for n in doc["@graph"] for t in ([n["@type"]] if isinstance(n["@type"], str) else n["@type"])}
    assert {"prov:Activity", "agento:ExecutionStep", "agento:KnowledgeBase", "agento:LanguageModel",
            "agento:Tool", "prov:SoftwareAgent", "agento:Agent"} <= types
    activity = next(n for n in doc["@graph"] if "prov:Activity" in n["@type"])
    assert "urn:ura-kernel:release:1.1.0" in activity["prov:used"]


def test_the_export_carries_no_user_id_and_is_valid_json_ld() -> None:
    from app.audit.provenance import export

    text = json.dumps(export([_row()]))
    assert "taxpayer-42" not in text
    rdflib = pytest.importorskip("rdflib")
    assert len(rdflib.Graph().parse(data=text, format="json-ld")) > 20


# --------------------------------------------------------------------------- benchmark harness


def _bench():
    spec = importlib.util.spec_from_file_location("bench_llm_concurrency", ROOT / "scripts/bench_llm_concurrency.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_llm_concurrency"] = module
    spec.loader.exec_module(module)
    return module


def test_the_benchmark_reports_percentiles_and_errors() -> None:
    bench = _bench()
    assert bench.percentile([1.0, 2.0, 3.0, 4.0, 10.0], 95) == 10.0
    calls = {"n": 0}

    async def send(prompt: str) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("timeout")
        await asyncio.sleep(0.001)

    report = asyncio.run(bench.run_level(send, concurrency=4, requests=8))
    assert (report.requests, report.errors) == (8, 1)
    assert report.meets_target is False, "an error fails the level whatever the latency"


# --------------------------------------------------------------------------- tool


def test_the_penalty_tool_is_gated_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools import ToolRegistry

    tool = ToolRegistry.get("kernel_compute_penalty")
    monkeypatch.delenv("FLAG_TAX_GRAPH", raising=False)
    assert tool.execute(rule="efris_penal_tax", amount=1)["ok"] is False
    monkeypatch.setenv("FLAG_TAX_GRAPH", "true")
    assert tool.execute(rule="efris_penal_tax", amount=50_000, date="2026-08-15")["amount"] == 200_000.0
    assert "whole number" in tool.execute(rule="late_payment_interest", amount=1, months=2.5)["error"]
    assert tool.execute(rule="invented_rule", amount=1)["ok"] is False
