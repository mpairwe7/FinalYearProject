"""Unit tests for ``scripts/reindex_vectorize.py`` (no network, no wrangler).

The CLI populates the Vectorize index the retriever's dense fallback queries —
a malformed NDJSON row or a silently wrong wrangler argv corrupts hybrid
retrieval on the Crane Cloud profile, so the row format, the embed batching,
the subprocess argv, and the unconfigured guard each get pinned here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
import unittest
import unittest.mock as mock
from pathlib import Path

from app.providers import config

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reindex_vectorize.py"
_spec = importlib.util.spec_from_file_location("reindex_vectorize", _SCRIPT)
ri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ri)

_DOCS = [
    {
        "text": "VAT standard rate is 18 percent.",
        "source": "vat.pdf",
        "page": 2,
        "section": "S1",
        "tag": "vat",
        "chunk_id": "c1",
        "doc_type": "pdf",
    },
    {
        "text": "T" * 3000,  # exercises the 2000-char metadata truncation
        "source": "tin.csv",
        "page": "",
        "section": "",
        "tag": "tin",
        "chunk_id": "c2",
        "doc_type": "csv",
    },
]


class NdjsonTest(unittest.TestCase):
    def test_rows_match_wrangler_shape_and_deterministic_ids(self):
        import tempfile

        vectors = [[0.1] * 4, [0.2] * 4]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "vecs.ndjson"
            n = ri.to_ndjson(_DOCS, vectors, out)
            lines = out.read_text(encoding="utf-8").splitlines()

        self.assertEqual(n, 2)
        self.assertEqual(len(lines), 2)
        rows = [json.loads(line) for line in lines]
        for doc, vec, row in zip(_DOCS, vectors, rows, strict=True):
            self.assertEqual(row["id"], ri.deterministic_point_id(doc))
            self.assertEqual(row["values"], vec)
            self.assertEqual(row["metadata"]["source"], doc["source"])
            self.assertEqual(row["metadata"]["tag"], doc["tag"])
        self.assertEqual(len(rows[1]["metadata"]["text"]), 2000)  # truncated

    def test_length_mismatch_is_an_error_not_silent_drop(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "vecs.ndjson"
            with self.assertRaises(ValueError):  # zip(strict=True)
                ri.to_ndjson(_DOCS, [[0.1] * 4], out)


class EmbedBatchTest(unittest.TestCase):
    def test_batches_respect_embed_batch_size(self):
        texts = [f"text {i}" for i in range(5)]
        calls: list[list[str]] = []

        def fake_embed(batch):
            calls.append(list(batch))
            return [[0.5] * 4 for _ in batch]

        with mock.patch.object(ri, "EMBED_BATCH", 2), \
             mock.patch.object(ri, "time", types.SimpleNamespace(sleep=lambda s: None)), \
             mock.patch.object(ri.gw, "workers_ai_embed", side_effect=fake_embed):
            vectors = ri.embed_all(texts)

        self.assertEqual(len(vectors), 5)
        self.assertEqual([len(c) for c in calls], [2, 2, 1])
        self.assertEqual(calls[0], ["text 0", "text 1"])


class WranglerTest(unittest.TestCase):
    def test_invokes_fixed_argv_with_check(self):
        with mock.patch.object(ri.subprocess, "run") as run:
            ri._wrangler(["vectorize", "insert", "ura-kb", "--file", "/tmp/x.ndjson"])
        run.assert_called_once_with(
            [ri.WRANGLER, "vectorize", "insert", "ura-kb", "--file", "/tmp/x.ndjson"],
            check=True,
        )


class MainGuardTest(unittest.TestCase):
    def test_exits_when_vectorize_unconfigured(self):
        for k in (
            "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
            "CF_AIG_GATEWAY", "CF_AIG_TOKEN", "VECTORIZE_INDEX",
        ):
            os.environ.pop(k, None)
        config.get_cloud_settings.cache_clear()
        self.addCleanup(config.get_cloud_settings.cache_clear)
        with mock.patch.object(sys, "argv", ["reindex_vectorize.py"]):
            with self.assertRaises(SystemExit) as raised:
                ri.main()
        self.assertIn("not configured", str(raised.exception))


class VerifyTest(unittest.TestCase):
    def test_verify_embeds_query_and_prints_hits(self):
        hits = [{"score": 0.91, "source": "vat.pdf", "text": "VAT is 18%"}]
        with mock.patch.object(ri.gw, "workers_ai_embed", return_value=[[0.3] * 4]) as emb, \
             mock.patch("app.providers.vectorize.vectorize_query", return_value=hits) as vq:
            ri._verify("What is the VAT rate?")
        emb.assert_called_once_with(["What is the VAT rate?"])
        vq.assert_called_once()


if __name__ == "__main__":
    unittest.main()
