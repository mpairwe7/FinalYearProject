"""The runtime's dependency on the training tree is frozen, and visible.

Issue #308. `app/` imports from `ml/` — the training package — and the image
does not ship `ml/` wholesale. That combination is how the language detector
came to be missing in production for the life of the deployment: the import
failed, the code fell back to a character heuristic, the fallback logged at
debug, and every Luganda question was read as English. Nothing crashed and
nothing said anything.

Two guards, neither of which pretends the coupling is fixed:

* the import inventory is pinned, so a *new* runtime dependency on `ml/`
  is a deliberate act with a test to update rather than something that
  quietly rides along and breaks a deploy months later; and
* optional capabilities must report the backend they actually resolved to,
  so the next silent degradation is legible from `/ready`.

Removing an entry is always fine — that is the direction of travel. Adding
one requires a decision about whether the image ships it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Modules under app/ that import from ml/, and why they are tolerated today.
# Each is debt: the runtime should not need the training tree.
KNOWN_ML_IMPORTERS: dict[str, str] = {
    "query.py": "lang_id — lingua language detection; shipped explicitly in the image",
    "speech_service.py": "ASR/MT/TTS inference wrappers; speech is disabled on CPU deploys",
    "pdf_corpus.py": "chunkers, used at corpus-build time rather than per request",
    "crawl_corpus.py": "chunkers, used at corpus-build time rather than per request",
}

_ML_IMPORT = re.compile(r"^\s*(?:from\s+ml[\s.]|import\s+ml\b)", re.M)


class RuntimeImportBoundaryTest(unittest.TestCase):
    def _actual_importers(self) -> set[str]:
        found = set()
        for path in APP_DIR.rglob("*.py"):
            if _ML_IMPORT.search(path.read_text(encoding="utf-8")):
                found.add(str(path.relative_to(APP_DIR)))
        return found

    def test_no_new_runtime_dependency_on_the_training_tree(self) -> None:
        added = self._actual_importers() - set(KNOWN_ML_IMPORTERS)
        self.assertEqual(
            added,
            set(),
            "new runtime import of ml/ in "
            f"{sorted(added)} — the image ships ml/ file-by-file, so an import "
            "that is not copied will fail at runtime and degrade silently. "
            "Either move the code into app/, or copy it in Dockerfile.cranecloud "
            "and add it to KNOWN_ML_IMPORTERS with the reason.",
        )

    def test_the_inventory_does_not_list_modules_that_have_been_cleaned_up(self) -> None:
        """Keeps the debt list honest as modules are migrated off ml/."""
        stale = set(KNOWN_ML_IMPORTERS) - self._actual_importers()
        self.assertEqual(
            stale, set(), f"KNOWN_ML_IMPORTERS lists {sorted(stale)}, which no longer import ml/"
        )


class CapabilityReportingTest(unittest.TestCase):
    def test_language_detection_backend_names_the_resolved_backend(self) -> None:
        from app.query import language_detection_backend

        self.assertIn(language_detection_backend(), {"lingua", "heuristic"})

    def test_health_response_carries_capabilities(self) -> None:
        from app.models import HealthResponse

        payload = HealthResponse(
            status="ready",
            version="1.2.0",
            model_loaded=True,
            capabilities={"language_detection": "lingua"},
        ).model_dump()
        self.assertEqual(payload["capabilities"]["language_detection"], "lingua")

    def test_capabilities_defaults_to_empty_rather_than_none(self) -> None:
        """Absent capabilities must not serialize as null into the contract."""
        from app.models import HealthResponse

        payload = HealthResponse(status="ready", version="1.2.0", model_loaded=True).model_dump()
        self.assertEqual(payload["capabilities"], {})


if __name__ == "__main__":
    unittest.main()
