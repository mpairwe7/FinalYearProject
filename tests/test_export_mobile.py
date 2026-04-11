"""Unit tests for ``ml/scripts/export_mobile.py`` (2026 mobile exporter).

Coverage:
    * Tool discovery — ``LlamaCppTools`` resolution under env-var override
    * Adapter discovery — ``find_latest_adapter``, ``read_adapter_lineage``
    * Hashing — ``file_sha256``, ``_atomic_copy`` integrity check
    * Manifest + model card generation
    * Quant registry — IQ-series + ``QUANTS_REQUIRING_IMATRIX`` constants
    * CLI parser — every new 2026 flag is exposed by ``--help``
    * Dry-run — exits cleanly with no adapter and with a fake adapter

The script uses dataclasses with ``Optional[Path]`` annotations which
break ``importlib.util.spec_from_file_location`` loading on Python 3.10
(the dataclass decorator can't resolve forward refs when the module
isn't in ``sys.modules``). We work around this by registering the
module in ``sys.modules`` before exec'ing the spec.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

EXPORT_MOBILE_PATH = PROJECT_ROOT / "ml" / "scripts" / "export_mobile.py"


@pytest.fixture(scope="session")
def em():
    """Load export_mobile.py once per session.

    The dataclass decorators inside the script need the module to be
    findable in ``sys.modules`` to resolve ``Optional[Path]`` annotations
    — registering it before ``exec_module`` makes that work.
    """
    spec = importlib.util.spec_from_file_location(
        "export_mobile_under_test", str(EXPORT_MOBILE_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_mobile_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Constants + registry
# ---------------------------------------------------------------------------


class TestConstants:
    def test_pipeline_version_set(self, em):
        assert em.PIPELINE_VERSION
        assert em.SCHEMA_VERSION

    def test_quant_registry_has_iq_series(self, em):
        # 2024 IQ-series quants must be present (better than Q4_K_M for mobile)
        for q in ("IQ4_NL", "IQ4_XS", "IQ3_M", "IQ3_S", "IQ2_M"):
            assert q in em.QUANT_TYPES, f"missing {q} from QUANT_TYPES"
        # And the K-series classics
        for q in ("Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "F16"):
            assert q in em.QUANT_TYPES

    def test_imatrix_required_set(self, em):
        # Sub-3-bit quants are essentially unusable without imatrix
        for q in ("IQ3_M", "IQ3_S", "IQ2_M"):
            assert q in em.QUANTS_REQUIRING_IMATRIX
        # 4-bit and above don't strictly need imatrix
        for q in ("Q4_K_M", "Q5_K_M", "IQ4_NL"):
            assert q not in em.QUANTS_REQUIRING_IMATRIX

    def test_default_quant_is_q4_k_m(self, em):
        assert em.DEFAULT_QUANT == "Q4_K_M"

    def test_mobile_filename_matches_flutter_config(self, em):
        # The Flutter OnDeviceLlmConfig hardcodes this path:
        # lib/core/inference/on_device_llm.dart : modelPath = 'models/ura-gemma-2b-q4_k_m.gguf'
        assert em.DEFAULT_MOBILE_FILENAME == "ura-gemma-2b-q4_k_m.gguf"

    def test_mobile_paths_resolve_under_project_root(self, em):
        assert em.ANDROID_ASSETS.is_relative_to(em.PROJECT_ROOT)
        assert em.IOS_STAGING.is_relative_to(em.PROJECT_ROOT)
        # And they point at the right Flutter project
        assert "MobileApp/ura_chatbot/android" in str(em.ANDROID_ASSETS)
        assert "MobileApp/ura_chatbot/ios" in str(em.IOS_STAGING)


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


class TestToolDiscovery:
    def test_discover_returns_struct_even_when_nothing_found(self, em, monkeypatch, tmp_path):
        # Stub the candidate dir list to a single empty temp dir + clear
        # PATH so neither file-system nor PATH discovery can find anything.
        monkeypatch.setattr(
            em, "_candidate_llama_cpp_dirs",
            lambda: [tmp_path / "nonexistent"],
        )
        monkeypatch.setenv("PATH", "")
        tools = em.discover_llama_cpp()
        assert isinstance(tools, em.LlamaCppTools)
        # All four binaries should be None
        assert tools.convert_script is None
        assert tools.quantize_bin is None
        assert tools.has_convert is False
        assert tools.has_quantize is False
        assert tools.has_imatrix is False

    def test_discover_finds_env_var_override(self, em, tmp_path, monkeypatch):
        # Build a fake llama.cpp dir with a stub convert script
        fake_repo = tmp_path / "fake_llama_cpp"
        (fake_repo / "build" / "bin").mkdir(parents=True)
        (fake_repo / "convert_hf_to_gguf.py").write_text("# fake")
        # Stub binaries (need to be marked executable for discover_llama_cpp)
        for name in ("llama-quantize", "llama-imatrix", "llama-cli"):
            stub = fake_repo / "build" / "bin" / name
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)

        # Restrict the candidate list to JUST the fake repo so the test
        # doesn't pick up a real llama.cpp on the host machine.
        monkeypatch.setattr(em, "_candidate_llama_cpp_dirs", lambda: [fake_repo])
        monkeypatch.setenv("PATH", "")
        tools = em.discover_llama_cpp()

        assert tools.convert_script == fake_repo / "convert_hf_to_gguf.py"
        assert tools.quantize_bin == fake_repo / "build" / "bin" / "llama-quantize"
        assert tools.imatrix_bin == fake_repo / "build" / "bin" / "llama-imatrix"
        assert tools.cli_bin == fake_repo / "build" / "bin" / "llama-cli"
        assert tools.repo_root == fake_repo
        assert tools.has_convert and tools.has_quantize and tools.has_imatrix


# ---------------------------------------------------------------------------
# Adapter discovery + lineage
# ---------------------------------------------------------------------------


class TestAdapterDiscovery:
    def test_find_latest_adapter_returns_none_when_empty(self, em, tmp_path, monkeypatch):
        monkeypatch.setattr(em, "ARTIFACTS_DIR", tmp_path)
        assert em.find_latest_adapter() is None

    def test_find_latest_adapter_picks_most_recent(self, em, tmp_path, monkeypatch):
        # Create two fake fine-tune outputs with different mtimes
        for name, mtime in (("ura-old", 1000), ("ura-new", 2000)):
            d = tmp_path / name / "final"
            d.mkdir(parents=True)
            (d / "adapter_config.json").write_text("{}")
            import os
            os.utime(d, (mtime, mtime))

        monkeypatch.setattr(em, "ARTIFACTS_DIR", tmp_path)
        latest = em.find_latest_adapter()
        assert latest is not None
        assert "ura-new" in str(latest)

    def test_find_latest_adapter_accepts_full_model(self, em, tmp_path, monkeypatch):
        # Adapter dir without adapter_config.json but with config.json (full model save)
        d = tmp_path / "ura-merged" / "final"
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")

        monkeypatch.setattr(em, "ARTIFACTS_DIR", tmp_path)
        assert em.find_latest_adapter() == d

    def test_read_adapter_lineage_from_training_config(self, em, tmp_path):
        adapter = tmp_path / "ura-test" / "final"
        adapter.mkdir(parents=True)
        # training_config.json is one level up from /final (matches fine_tune_gemma.py)
        (adapter.parent / "training_config.json").write_text(json.dumps({
            "pipeline_version": "2026.1.0",
            "dataset_metadata": {
                "train_sha256": "abc123",
                "git_commit": "deadbeef",
                "pipeline_version": "2026.1.0",
            },
            "lora_config": {"r": 16, "lora_alpha": 32, "use_rslora": True},
            "seed": 42,
        }))
        (adapter / "adapter_config.json").write_text(json.dumps({
            "base_model_name_or_path": "google/gemma-2-2b-it",
        }))

        lineage = em.read_adapter_lineage(adapter)
        assert lineage["pipeline_version"] == "2026.1.0"
        assert lineage["dataset_metadata"]["train_sha256"] == "abc123"
        assert lineage["lora_config"]["use_rslora"] is True
        assert lineage["base_model_id"] == "google/gemma-2-2b-it"
        assert "_source" in lineage

    def test_read_adapter_lineage_returns_empty_when_missing(self, em, tmp_path):
        adapter = tmp_path / "empty" / "final"
        adapter.mkdir(parents=True)
        lineage = em.read_adapter_lineage(adapter)
        assert lineage == {}


# ---------------------------------------------------------------------------
# Hashing + atomic copy
# ---------------------------------------------------------------------------


class TestHashingAndCopy:
    def test_file_sha256_is_stable(self, em, tmp_path):
        p = tmp_path / "x.bin"
        p.write_bytes(b"\x00\x01\x02\x03" * 1024)
        d1 = em.file_sha256(p)
        d2 = em.file_sha256(p)
        assert d1 == d2
        assert len(d1) == 64

    def test_atomic_copy_verifies_sha(self, em, tmp_path):
        src = tmp_path / "src.gguf"
        src.write_bytes(b"GGUF" + b"\x00" * 8192)
        dst = tmp_path / "android" / "models" / "out.gguf"
        sha = em._atomic_copy(src, dst)
        assert dst.exists()
        assert em.file_sha256(dst) == sha
        assert em.file_sha256(src) == sha
        # No leftover .tmp
        assert not (dst.with_suffix(".gguf.tmp")).exists()

    def test_atomic_copy_creates_parent_dirs(self, em, tmp_path):
        src = tmp_path / "src.gguf"
        src.write_bytes(b"data")
        dst = tmp_path / "deeply" / "nested" / "out.gguf"
        em._atomic_copy(src, dst)
        assert dst.exists()


# ---------------------------------------------------------------------------
# Manifest + model card
# ---------------------------------------------------------------------------


class TestManifestAndModelCard:
    def _build_manifest(self, em, tmp_path):
        gguf = tmp_path / "ura-gemma-2b-q4_k_m.gguf"
        gguf.write_bytes(b"GGUF" + b"\x00" * 4096)
        validation = em.ValidationResult(
            can_load=True,
            test_prompt="What is VAT?",
            test_output="The standard VAT rate in Uganda is 18%.",
        )
        return em.build_manifest(
            gguf, tmp_path,
            adapter_path=tmp_path / "fake_adapter",
            quant_type="Q4_K_M",
            base_model="google/gemma-2-2b-it",
            lineage={
                "pipeline_version": "2026.1.0",
                "effective_batch_size": 16,
                "learning_rate": 2e-4,
                "num_epochs": 3,
                "seed": 42,
                "lora_config": {
                    "r": 16, "lora_alpha": 32, "lora_dropout": 0.05,
                    "use_rslora": True, "use_dora": False,
                },
                "dataset_metadata": {
                    "train_path": "artifacts/training_data/train.messages.jsonl",
                    "train_sha256": "deadbeef" * 1,
                    "manifest_sha256": "1234abcd",
                    "pipeline_version": "2026.1.0",
                    "schema_version": "2026.1",
                    "git_commit": "abcdef123456",
                },
            },
            validation=validation,
        )

    def test_manifest_has_required_fields(self, em, tmp_path):
        manifest = self._build_manifest(em, tmp_path)
        assert manifest.pipeline_version == em.PIPELINE_VERSION
        assert manifest.schema_version == em.SCHEMA_VERSION
        assert manifest.model_name == "ura-gemma-2b"
        assert manifest.base_model == "google/gemma-2-2b-it"
        assert manifest.quantization == "Q4_K_M"
        assert manifest.size_bytes > 0
        assert len(manifest.sha256) == 64
        assert manifest.validation["can_load"] is True
        # Deployment block has both platforms
        assert "android" in manifest.deployment
        assert "ios" in manifest.deployment
        assert manifest.deployment["android"]["min_sdk"] == 24
        assert manifest.deployment["android"]["no_compress"] is True
        assert manifest.runtime["context_length"] == 1024

    def test_manifest_json_is_persisted(self, em, tmp_path):
        self._build_manifest(em, tmp_path)
        manifest_path = tmp_path / "mobile_manifest.json"
        assert manifest_path.exists()
        m = json.loads(manifest_path.read_text())
        assert m["pipeline_version"] == em.PIPELINE_VERSION
        assert m["lineage"]["lora_config"]["use_rslora"] is True

    def test_model_card_renders_with_lineage(self, em, tmp_path):
        manifest = self._build_manifest(em, tmp_path)
        out = em.write_model_card(manifest, tmp_path / "MODEL_CARD.md")
        assert out.exists()
        text = out.read_text()
        # Lineage fields surface in the card
        assert "google/gemma-2-2b-it" in text
        assert "Q4_K_M" in text
        assert "2026.1.0" in text
        assert "abcdef123456" in text  # training git commit
        assert "use_rslora" in text or "RSLoRA" in text or "True" in text
        # Deployment instructions present
        assert "MediaPipe" in text
        assert "noCompress" in text
        assert "OnDeviceLlm" in text

    def test_model_card_handles_missing_lineage(self, em, tmp_path):
        gguf = tmp_path / "model.gguf"
        gguf.write_bytes(b"x" * 1024)
        manifest = em.build_manifest(
            gguf, tmp_path,
            adapter_path=tmp_path / "fake",
            quant_type="Q4_K_M",
            base_model="google/gemma-2-2b-it",
            lineage={},  # nothing
            validation=em.ValidationResult(),
        )
        # Should not crash even with empty lineage
        out = em.write_model_card(manifest, tmp_path / "MODEL_CARD.md")
        assert out.exists()
        text = out.read_text()
        assert "unknown" in text  # placeholder for missing lineage fields


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------


class TestDeploy:
    def test_deploy_writes_to_both_platforms(self, em, tmp_path, monkeypatch):
        # Stage a fake mobile project layout
        mobile_root = tmp_path / "mobile_root"
        android = mobile_root / "android" / "app" / "src" / "main" / "assets" / "models"
        ios_runner = mobile_root / "ios" / "Runner"
        ios_runner.mkdir(parents=True)
        # The deploy function checks ANDROID_ASSETS.parent.parent (= .../main)
        # so we must create at least that path; we mkdir all the way through
        # so the atomic copy can also write into models/.
        android.mkdir(parents=True, exist_ok=True)
        (mobile_root / "android" / "app" / "build.gradle.kts").write_text(
            'androidResources {\n  noCompress += listOf("gguf")\n}\n'
        )

        monkeypatch.setattr(em, "MOBILE_ROOT", mobile_root)
        monkeypatch.setattr(em, "ANDROID_ASSETS", android)
        monkeypatch.setattr(em, "IOS_STAGING", ios_runner / "models")

        # Build a quant model file + a manifest
        gguf = tmp_path / "ura-gemma-2b-q4_k_m.gguf"
        gguf.write_bytes(b"GGUF" + b"\xff" * 8192)
        manifest = em.build_manifest(
            gguf, tmp_path, adapter_path=tmp_path / "fake",
            quant_type="Q4_K_M", base_model="google/gemma-2-2b-it",
            lineage={}, validation=em.ValidationResult(),
        )

        deployed = em.deploy_to_mobile_app(gguf, manifest)
        assert deployed["android"] is not None
        assert deployed["ios"] is not None
        assert (android / em.DEFAULT_MOBILE_FILENAME).exists()
        assert (ios_runner / "models" / em.DEFAULT_MOBILE_FILENAME).exists()
        # Post-copy SHA matches
        src_sha = em.file_sha256(gguf)
        assert em.file_sha256(android / em.DEFAULT_MOBILE_FILENAME) == src_sha
        assert deployed["android"]["sha256"] == src_sha

    def test_deploy_skips_missing_platform(self, em, tmp_path, monkeypatch):
        # Only iOS exists; Android assets parent missing
        ios_runner = tmp_path / "mobile" / "ios" / "Runner"
        ios_runner.mkdir(parents=True)
        monkeypatch.setattr(em, "ANDROID_ASSETS", tmp_path / "nope" / "android" / "models")
        monkeypatch.setattr(em, "IOS_STAGING", ios_runner / "models")

        gguf = tmp_path / "model.gguf"
        gguf.write_bytes(b"data")
        manifest = em.build_manifest(
            gguf, tmp_path, adapter_path=tmp_path / "fake",
            quant_type="Q4_K_M", base_model="google/gemma-2-2b-it",
            lineage={}, validation=em.ValidationResult(),
        )
        deployed = em.deploy_to_mobile_app(gguf, manifest)
        assert deployed["android"] is None  # gracefully skipped
        assert deployed["ios"] is not None

    def test_deploy_respects_no_android_no_ios_flags(self, em, tmp_path, monkeypatch):
        gguf = tmp_path / "m.gguf"
        gguf.write_bytes(b"x")
        manifest = em.build_manifest(
            gguf, tmp_path, adapter_path=tmp_path / "fake",
            quant_type="Q4_K_M", base_model="b",
            lineage={}, validation=em.ValidationResult(),
        )
        # Both platforms disabled
        deployed = em.deploy_to_mobile_app(
            gguf, manifest, deploy_android=False, deploy_ios=False
        )
        assert deployed == {"android": None, "ios": None}


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestCLI:
    def test_help_lists_2026_flags(self, em):
        import subprocess
        r = subprocess.run(
            [sys.executable, str(EXPORT_MOBILE_PATH), "--help"],
            capture_output=True, text=True, check=False,
        )
        assert r.returncode == 0
        out = r.stdout
        for flag in (
            "--imatrix",
            "--imatrix-source",
            "--imatrix-samples",
            "--no-deploy",
            "--no-android",
            "--no-ios",
            "--no-validate",
            "--keep-merged",
            "--keep-gguf-f16",
            "--dry-run",
            "--adapter",
            "--quant",
        ):
            assert flag in out, f"missing {flag} in --help"
        # Quant types in epilog
        for q in ("IQ4_NL", "IQ3_M", "Q4_K_M", "F16"):
            assert q in out, f"missing {q} in --help"

    def test_dry_run_no_adapter_exits_clean(self, em, tmp_path, monkeypatch):
        import subprocess
        # Empty artifacts dir → no adapter found → exit 2
        monkeypatch.setenv("LLAMA_CPP_DIR", "/nonexistent")
        # Run from a clean tmp cwd so it can't find a real artifact
        r = subprocess.run(
            [sys.executable, str(EXPORT_MOBILE_PATH), "--dry-run",
             "--adapter", str(tmp_path / "missing")],
            capture_output=True, text=True, check=False,
        )
        # --adapter explicitly passed but doesn't exist → exit 2
        assert r.returncode == 2

    def test_dry_run_with_fake_adapter(self, em, tmp_path):
        import subprocess
        adapter = tmp_path / "ura-fake" / "final"
        adapter.mkdir(parents=True)
        (adapter / "adapter_config.json").write_text(json.dumps({
            "base_model_name_or_path": "google/gemma-2-2b-it",
        }))
        (adapter / "adapter_model.safetensors").write_bytes(b"")

        r = subprocess.run(
            [sys.executable, str(EXPORT_MOBILE_PATH),
             "--adapter", str(adapter), "--dry-run"],
            capture_output=True, text=True, check=False,
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        # Dry run prints tool inventory
        combined = r.stdout + r.stderr
        assert "Dry run complete" in combined
        assert "adapter_config.json" in combined
