"""Local ASR and MT absent from a build are expected, not errors (G678)."""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from app import speech_service


class OptionalMlModuleTests(unittest.TestCase):
    def test_a_module_the_build_does_not_ship_is_none_and_logged_once_as_info(self) -> None:
        with self.assertLogs(speech_service.logger, level=logging.INFO) as logs:
            module = speech_service._optional_ml_module("ml.scripts.not_in_this_build.infer", "local ASR")

        self.assertIsNone(module)
        self.assertEqual([r.levelno for r in logs.records], [logging.INFO])
        self.assertIn("not in this build", logs.output[0])

    def test_a_missing_dependency_of_a_shipped_module_still_raises(self) -> None:
        missing_dependency = ModuleNotFoundError("No module named 'sherpa_onnx'", name="sherpa_onnx")
        with (
            mock.patch.object(speech_service.importlib, "import_module", side_effect=missing_dependency),
            self.assertRaises(ModuleNotFoundError),
        ):
            speech_service._optional_ml_module("ml.scripts.asr.infer_asr", "local ASR")

    def test_a_shipped_module_is_returned(self) -> None:
        self.assertIsNotNone(speech_service._optional_ml_module("ml.scripts.lang_id", "language ID"))


if __name__ == "__main__":
    unittest.main()
