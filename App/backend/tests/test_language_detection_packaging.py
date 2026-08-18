"""Luganda must survive the trip into the container image.

Found by asking the deployed system a Luganda question during prototype
rehearsal: "Omusolo gwa VAT guli gwa bbeeyi ki mu Uganda?" came back with
locale="en" and retrieval_mode="abstained", while the identical text
detected as "lg" locally.

query.detect_language() reaches for ml.scripts.lang_id (the lingua
backend) and, on ImportError, sets _LANG_DETECTOR_INIT_FAILED and drops to
a character heuristic that reads Luganda as English. The image never
copied ml/, so every deployment ran on that heuristic — silently, because
the fallback is deliberately quiet and nothing in the response says which
backend answered. lingua itself was installed the whole time; only the
96-line module was missing.

Two guards, because either alone is blind:

* the behavioural test passes on a source checkout whether or not the
  image is correct, since the repo root is on sys.path here; and
* the packaging test is what actually fails when the COPY is dropped
  again, but it cannot tell whether detection still works.

The packaging assertion is deliberately about placement, not just
presence: uvicorn runs with directory=/app/backend and no PYTHONPATH
(deploy/cranecloud/supervisord.conf), so ml/ has to land beside app/ for
the import to resolve at all.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND.parents[1]
DOCKERFILE = _BACKEND.parent / "Dockerfile.cranecloud"

# CI runs this suite as `working-directory: App/backend` with `PYTHONPATH=.`,
# which is the same shape as the container: `app` resolves, `ml` does not.
# Without this the behavioural tests below would assert the heuristic's answer
# rather than the lingua backend's — passing while describing the bug. Put the
# repo root on the path so they exercise what the fixed image now ships.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.query import detect_language  # noqa: E402  (import after path setup)


class LugandaDetectionTest(unittest.TestCase):
    def test_luganda_is_not_read_as_english(self) -> None:
        """The rehearsal question, and a second phrasing with no English tokens."""
        for text in (
            "Omusolo gwa VAT guli gwa bbeeyi ki mu Uganda?",
            "Nkola ntya okuwandiisa TIN?",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_language(text), "lg")

    def test_english_and_swahili_still_route_correctly(self) -> None:
        self.assertEqual(detect_language("What is the VAT rate in Uganda?"), "en")
        self.assertEqual(detect_language("Kodi ya VAT ni kiasi gani?"), "sw")

    def test_the_lingua_backend_is_actually_reachable(self) -> None:
        """Guards the silent degradation itself, not just its symptom."""
        from app import query

        self.assertIsNotNone(
            query._get_language_detector(),
            "lingua backend unavailable — detection has fallen back to the "
            "character heuristic, which reads Luganda as English",
        )


class ImagePackagingTest(unittest.TestCase):
    def test_dockerfile_ships_lang_id_beside_the_app_package(self) -> None:
        body = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "ml/scripts/lang_id.py",
            body,
            "Dockerfile.cranecloud no longer copies ml/scripts/lang_id.py; the "
            "container will fall back to the heuristic and mis-detect Luganda",
        )
        for module in ("ml/__init__.py", "ml/scripts/__init__.py"):
            self.assertIn(module, body, f"{module} missing — `import ml` will fail")

    def test_lang_id_lands_on_the_path_uvicorn_actually_uses(self) -> None:
        """/app/backend, not /app — see the supervisord note in the docstring."""
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
            if re.match(r"\s*COPY\s+ml/scripts/lang_id\.py", line):
                self.assertIn(
                    "/app/backend/ml/scripts/lang_id.py",
                    line,
                    "lang_id.py must be copied beside app/ (uvicorn runs from "
                    "/app/backend with no PYTHONPATH), not to /app",
                )
                return
        self.fail("no COPY line for ml/scripts/lang_id.py in Dockerfile.cranecloud")


if __name__ == "__main__":
    unittest.main()
