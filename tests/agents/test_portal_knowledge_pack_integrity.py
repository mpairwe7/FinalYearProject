"""The declarative portal pack is shown to taxpayers as official steps (G246).

ADR-0012 added ``portal_navigation_overrides.json`` and its loader. The loader
only ever added: a workflow removed from the pack was still served until the
process restarted. It checked no types or links, a pack could re-point any
portal's ``canonical_url`` anywhere, and the module-level dicts were patched
in place with no lock. The content itself told taxpayers to submit an NTR
refund through an online "Refund Application" using Form DT-3008, where the
corpus says that refund is manual and delivered to a domestic tax office, and
described a "Form DT-10307" voluntary disclosure with a "100% waiver" that
URA's own FAQ does not describe (it names a VDF and lists exclusions).
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "App" / "backend"))

from app.tools import ToolRegistry  # noqa: E402
from app.tools import portal_navigator_data as pnd  # noqa: E402
from app.verified_resources import is_authoritative_ura_url  # noqa: E402

PACK = REPO_ROOT / "App" / "backend" / "app" / "tools" / "data" / "portal_navigation_overrides.json"


@pytest.fixture(autouse=True)
def restore_shipped_pack():
    yield
    pnd.load_portal_knowledge_pack()


def _workflow(**overrides) -> dict:
    spec = {
        "title": "A test workflow",
        "portal_key": "prn_payments",
        "url": "https://ura.go.ug",
        "steps": ["Open https://ura.go.ug and check the PRN."],
        "sources": ["Data/dataset/ura_make_a_payment_faqs.csv"],
    }
    spec.update(overrides)
    return spec


def _write(tmp_path: Path, payload: dict | str, name: str = "pack.json") -> Path:
    path = tmp_path / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


class TestTheShippedPack:
    def test_it_loads_with_nothing_rejected(self) -> None:
        result = pnd.load_portal_knowledge_pack()
        assert result["loaded"] is True
        assert result["rejected"] == []
        assert result["workflow_count"] == len(json.loads(PACK.read_text(encoding="utf-8"))["workflows"])

    def test_every_workflow_cites_sources_that_exist(self) -> None:
        for key, spec in json.loads(PACK.read_text(encoding="utf-8"))["workflows"].items():
            assert spec.get("sources"), key
            for source in spec["sources"]:
                if source.startswith("Data/"):
                    assert (REPO_ROOT / source).is_file(), (key, source)
                else:
                    assert is_authoritative_ura_url(source), (key, source)

    def test_the_ntr_refund_steps_match_the_corpus(self) -> None:
        steps = " ".join(pnd.get_portal_workflow("mda_ntr_prn_reconciliation")["steps"])
        # ura_non_tax_revenues_faqs.csv: DT-3008 is manual and delivered in person.
        assert "DT-3008" in steps and "deliver it to your local URA domestic tax office" in steps
        assert "Refund Application" not in steps

    def test_the_voluntary_disclosure_steps_match_uras_faq(self) -> None:
        steps = " ".join(pnd.get_portal_workflow("voluntary_disclosure_and_instalments")["steps"])
        assert "Voluntary Disclosure Form (VDF)" in steps and "services@ura.go.ug" in steps
        assert "under audit or investigation" in steps  # the exclusions are stated
        for unsupported in ("DT-10307", "100%", "25% down-payment"):
            assert unsupported not in steps


class TestEveryLoadValidatesAndReplaces:
    def test_a_removed_workflow_stops_being_served(self, tmp_path: Path) -> None:
        both = _write(tmp_path, {"workflows": {"wf_one": _workflow(), "wf_two": _workflow()}}, "a.json")
        assert pnd.load_portal_knowledge_pack(both)["workflow_count"] == 2
        one = _write(tmp_path, {"workflows": {"wf_one": _workflow()}}, "b.json")
        assert pnd.load_portal_knowledge_pack(one)["workflow_count"] == 1
        assert pnd.get_portal_workflow("wf_two") is None
        assert pnd.get_portal_workflow("wf_one") is not None

    @pytest.mark.parametrize(
        "bad",
        [
            {"steps": ["Pay at https://ura-refunds.example.com/claim"]},
            {"url": "http://ura.go.ug"},
            {"url": "https://portal.ura.go.ug@evil.example/"},
            {"sources": []},
            {"sources": ["Data/../../etc/passwd"]},
            {"steps": "Open the portal"},
            {"portal_key": "no_such_portal"},
            {"title": ""},
        ],
    )
    def test_an_invalid_workflow_is_skipped_and_reported(self, tmp_path: Path, bad: dict) -> None:
        path = _write(tmp_path, {"workflows": {"wf_bad": _workflow(**bad), "wf_good": _workflow()}})
        result = pnd.load_portal_knowledge_pack(path)
        assert result["rejected"] == ["workflow:wf_bad"]
        assert pnd.get_portal_workflow("wf_bad") is None
        assert pnd.get_portal_workflow("wf_good") is not None

    def test_a_portal_can_only_be_repointed_to_ura_and_reverts(self, tmp_path: Path) -> None:
        original = dict(pnd.EXTERNAL_URA_PORTALS["e_services"])
        path = _write(
            tmp_path,
            {
                "portals": {
                    "e_services": {"canonical_url": "https://portal.ura.go.ug/new"},
                    "efris": {"canonical_url": "https://efris-login.example.net"},
                    "brand_new_portal": {"canonical_url": "https://ura.go.ug/x"},
                }
            },
        )
        result = pnd.load_portal_knowledge_pack(path)
        assert sorted(result["rejected"]) == ["portal:brand_new_portal", "portal:efris"]
        assert pnd.EXTERNAL_URA_PORTALS["e_services"]["canonical_url"] == "https://portal.ura.go.ug/new"
        assert pnd.EXTERNAL_URA_PORTALS["e_services"]["name"] == original["name"]
        assert "brand_new_portal" not in pnd.EXTERNAL_URA_PORTALS
        pnd.load_portal_knowledge_pack()
        assert pnd.EXTERNAL_URA_PORTALS["e_services"] == original

    def test_a_malformed_pack_keeps_the_last_good_set(self, tmp_path: Path) -> None:
        good = _write(tmp_path, {"workflows": {"wf_one": _workflow()}}, "good.json")
        pnd.load_portal_knowledge_pack(good)
        broken = _write(tmp_path, '{"workflows": {"wf_one": ', "broken.json")
        result = pnd.load_portal_knowledge_pack(broken)
        assert result["loaded"] is False and "invalid pack" in result["error"]
        assert pnd.get_portal_workflow("wf_one") is not None


def test_a_workflow_for_another_portal_does_not_relabel_this_one() -> None:
    result = ToolRegistry.call(
        "navigate_external_portal",
        {"extracted_text": "EFRIS login fails", "portal_name": "efris", "workflow_intent": "mda_ntr_prn_reconciliation"},
    )
    assert result["screen_diagnostic"]["detected_state"] != "mda_ntr_prn_reconciliation"
    assert result["navigation_guidance"]["sources"] == []


def test_the_declarative_steps_carry_their_sources() -> None:
    result = ToolRegistry.call(
        "navigate_external_portal",
        {
            "extracted_text": "Paid PRN on Mobile Money but the agency portal still shows unpaid",
            "portal_name": "prn_payments",
            "workflow_intent": "mda_ntr_prn_reconciliation",
        },
    )
    assert result["screen_diagnostic"]["detected_state"] == "mda_ntr_prn_reconciliation"
    assert "Data/dataset/ura_non_tax_revenues_faqs.csv" in result["navigation_guidance"]["sources"]


def test_reloading_while_the_tool_reads_never_raises(tmp_path: Path) -> None:
    alt = _write(tmp_path, {"workflows": {"wf_one": _workflow()}})
    errors: list[BaseException] = []

    def reload_loop() -> None:
        try:
            for i in range(60):
                pnd._PACK_STATE["key"] = None  # force a real reload each time
                pnd.load_portal_knowledge_pack(alt if i % 2 else None)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def read_loop() -> None:
        try:
            for _ in range(200):
                pnd.get_portal_workflow("mda_ntr_prn_reconciliation")
                list(pnd.EXTERNAL_URA_PORTALS.items())
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=reload_loop)] + [threading.Thread(target=read_loop) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
