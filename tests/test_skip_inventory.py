"""Skipped tests are accounted for, and cannot quietly multiply.

Issue #312. A reader sees ~2,400 passing tests and infers coverage that the
skips silently contradict, so the risk is not that skipping is wrong — it is
that nobody knows what is skipped or why.

Auditing the actual set changed the shape of that issue. All 25 markers are
capability gates ("PyMuPDF not installed", "torch/peft not installed", "set
CF_LIVE_TEST=1 + real CF keys"), every one carries a reason string, and none
hides a known defect behind an unconditional skip. That is the correct
pattern: a test that cannot run in an environment should say so and run
everywhere it can, rather than being deleted or left red.

So this guard does not demand the skips go away. It pins the inventory, so a
skip appearing in a new file is a deliberate act with a test to update, and
it refuses skips that give no reason — which is the form that actually hides
things.

Deliberately not asserted: the *runtime* skip count. That legitimately varies
with the environment (torch present or not, optional parsers installed or
not), and pinning it would fail for reasons that have nothing to do with the
change under test.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOTS = (REPO_ROOT / "App" / "backend" / "tests", REPO_ROOT / "tests")

_SKIP_MARKER = re.compile(
    r"(@unittest\.skip\w*|pytest\.mark\.skipif|pytest\.mark\.skip\b|self\.skipTest|pytest\.skip)\s*\(",
)

# file (repo-relative) -> number of skip markers, with the reason they exist.
# Reducing a count or removing an entry is always fine. Adding one means a new
# test cannot run somewhere, which is worth a moment's thought.
KNOWN_SKIPS: dict[str, int] = {
    # Optional document parsers — openpyxl / python-docx / PyMuPDF / fpdf2.
    "App/backend/tests/test_documents.py": 6,
    "App/backend/tests/test_pdf_guards.py": 2,
    # Luganda eval corpus is not present in every checkout.
    "App/backend/tests/test_multilingual_routing.py": 2,
    # Live Cloudflare credentials, opt-in via CF_LIVE_TEST=1.
    "App/backend/tests/test_providers.py": 1,
    # FastAPI TestClient / lifespan smoke covered by the deploy smoke scripts.
    "tests/test_api.py": 5,
    # Agent-parity suites gated on optional backend fixtures.
    "tests/agents/test_backend_column_parity.py": 1,
    "tests/agents/test_backend_shim.py": 1,
    "tests/agents/test_identity_consent_parity.py": 1,
    "tests/agents/test_ticket_events.py": 1,
}


def _inventory() -> dict[str, int]:
    found: dict[str, int] = {}
    for root in TEST_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            count = len(_SKIP_MARKER.findall(path.read_text(encoding="utf-8")))
            if count:
                found[str(path.relative_to(REPO_ROOT))] = count
    return found


class SkipInventoryTest(unittest.TestCase):
    def test_no_new_file_starts_skipping_tests(self) -> None:
        added = sorted(set(_inventory()) - set(KNOWN_SKIPS))
        self.assertEqual(
            added,
            [],
            f"{added} newly skips tests. If it is a capability gate, add it to "
            "KNOWN_SKIPS with the reason. If it is a known defect, open an "
            "issue and reference it at the skip — a skip with no issue behind "
            "it is how coverage is lost quietly.",
        )

    def test_skip_counts_do_not_creep_upward(self) -> None:
        actual = _inventory()
        grew = {
            path: (KNOWN_SKIPS[path], actual[path])
            for path in KNOWN_SKIPS
            if path in actual and actual[path] > KNOWN_SKIPS[path]
        }
        self.assertEqual(grew, {}, f"skip count rose in {grew} (expected, actual)")

    def test_the_inventory_does_not_list_files_that_stopped_skipping(self) -> None:
        """Keeps the list honest as gates are removed."""
        actual = _inventory()
        stale = sorted(p for p in KNOWN_SKIPS if p not in actual)
        self.assertEqual(stale, [], f"KNOWN_SKIPS lists {stale}, which no longer skip anything")

    def test_every_skip_states_a_reason(self) -> None:
        """An unexplained skip is the form that actually hides a defect."""
        unexplained: list[str] = []
        for root in TEST_ROOTS:
            if not root.is_dir():
                continue
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for match in _SKIP_MARKER.finditer(text):
                    window = text[match.end() : match.end() + 220]
                    if '"' not in window and "'" not in window:
                        line = text[: match.start()].count("\n") + 1
                        unexplained.append(f"{path.relative_to(REPO_ROOT)}:{line}")
        self.assertEqual(unexplained, [], f"skip with no reason string: {unexplained}")


if __name__ == "__main__":
    unittest.main()
