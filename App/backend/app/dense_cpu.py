"""CPU embeddings for the local dense retrieval leg (ADR-0034).

``intfloat/multilingual-e5-small`` through ONNX Runtime: 384 dimensions, about
100 languages, no torch. The CPU deployments serve a sparse-only Qdrant
collection, so without this leg a question that shares no word with the right
passage finds nothing; with it, BM25 and this dense leg are fused by RRF.

The model files are pinned by revision and SHA-256. CI downloads and verifies
them once, embeds the corpus with them (``pgvector_dense build-artifact``) and
bakes both into the image, so the Space embeds queries with the very files that
embedded the passages. A different file would put queries in another vector
space and return confident nonsense, so :func:`verify_model_files` refuses one.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._root import APP_DATA_ROOT

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

MODEL_REPO = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
#: The files the embedder reads, with their SHA-256 at :data:`MODEL_REVISION`.
#: These digests are integrity pins of public files, not secrets; the markers on
#: each line tell Gitleaks and detect-secrets so.
#: fp32 rather than the int8 export: integer kernels can differ between the CPU
#: that builds the index (a CI runner) and the one that serves queries.
MODEL_FILES: dict[str, str] = {
    "onnx/model.onnx": "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",  # gitleaks:allow  # pragma: allowlist secret
    "tokenizer.json": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",  # gitleaks:allow  # pragma: allowlist secret
}
DIM = 384
#: e5 is trained with these prefixes; leaving them off degrades retrieval.
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
MAX_PASSAGE_TOKENS = 512
MAX_QUERY_TOKENS = 128

#: Where the image keeps the model and the corpus vectors.
DENSE_DIR = Path(os.getenv("LOCAL_DENSE_DIR", str(APP_DATA_ROOT / "dense")))


def model_dir(root: Path | None = None) -> Path:
    return (root or DENSE_DIR) / "model"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_model_files(directory: Path) -> list[str]:
    """Problems with the model files in *directory*: each missing file or differing SHA-256."""
    problems = []
    for name, expected in MODEL_FILES.items():
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif _sha256(path) != expected:
            problems.append(f"{name}: SHA-256 differs from the pinned {expected[:12]}")
    return problems


def download_model(directory: Path) -> None:  # pragma: no cover - network, CI only
    """Fetch the pinned model files into *directory* and verify them."""
    from huggingface_hub import hf_hub_download

    directory.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        hf_hub_download(MODEL_REPO, name, revision=MODEL_REVISION, local_dir=str(directory))
    problems = verify_model_files(directory)
    if problems:
        raise RuntimeError("downloaded model files do not match the pins: " + "; ".join(problems))


class OnnxEmbedder:
    """Mean-pooled, L2-normalised e5 embeddings on the CPU."""

    def __init__(self, directory: Path, *, threads: int | None = None, verify: bool = True,
                 session: Any = None, tokenizer: Any = None) -> None:
        if session is None or tokenizer is None:
            if verify:
                problems = verify_model_files(directory)
                if problems:
                    raise RuntimeError("dense model files rejected: " + "; ".join(problems))
            import onnxruntime as ort
            from tokenizers import Tokenizer

            options = ort.SessionOptions()
            options.intra_op_num_threads = threads or int(os.getenv("LOCAL_DENSE_THREADS", "2"))
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(directory / "onnx/model.onnx"), options,
                                           providers=["CPUExecutionProvider"])
            tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self._session = session
        self._tokenizer = tokenizer
        self._inputs = {i.name for i in session.get_inputs()}
        # The tokenizer's truncation and padding are per-instance state.
        self._lock = threading.Lock()

    def _encode(self, texts: list[str], max_tokens: int, batch_size: int) -> np.ndarray:
        import numpy as np

        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        # Longest first, so each batch pads to texts of similar length: padding a
        # 40-token FAQ to a 512-token PDF chunk is most of the cost otherwise.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]), reverse=True)
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        with self._lock:
            self._tokenizer.enable_truncation(max_length=max_tokens)
            self._tokenizer.enable_padding()
            for start in range(0, len(order), batch_size):
                batch = order[start:start + batch_size]
                encoded = self._tokenizer.encode_batch([texts[i] for i in batch])
                ids = np.array([e.ids for e in encoded], dtype=np.int64)
                mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
                feed = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in self._inputs:
                    feed["token_type_ids"] = np.zeros_like(ids)
                hidden = self._session.run(None, feed)[0]
                if hidden.shape[-1] != DIM:
                    raise RuntimeError(f"model returned {hidden.shape[-1]} dimensions, expected {DIM}")
                summed = (hidden * mask[..., None]).sum(axis=1)
                pooled = summed / np.clip(mask.sum(axis=1, keepdims=True), 1, None)
                norms = np.linalg.norm(pooled, axis=1, keepdims=True)
                out[batch] = pooled / np.clip(norms, 1e-12, None)
        return out

    def embed_passages(self, texts: list[str], *, batch_size: int = 32) -> np.ndarray:
        return self._encode([PASSAGE_PREFIX + t for t in texts], MAX_PASSAGE_TOKENS, batch_size)

    def embed_query(self, text: str) -> np.ndarray:
        return self._encode([QUERY_PREFIX + text], MAX_QUERY_TOKENS, 1)[0]
