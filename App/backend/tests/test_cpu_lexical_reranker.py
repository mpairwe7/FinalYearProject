"""The word-overlap reranker of the torch-free image, scored like any other reranker (G698).

It lived only in the HF Space's build-time patch, which also replaced the score loop in
`_rerank` so that this reranker's candidates got no `score_norm`. `demote_stale_editions` skips
a candidate without one, and `hit_relevance` squashes an unset score of 0 to 0.5.
"""

from __future__ import annotations

from unittest import mock

import pytest

from app.rag import retriever as retriever_module
from app.rag.query import current_fiscal_year
from app.rag.retriever import (
    STALE_EDITION_YEARS,
    HybridRetriever,
    _CpuLexicalReranker,
    _sparse_only_reranker,
    fiscal_year_rank,
    gating_relevance,
)

VAT = "The VAT rate is 18 percent on taxable supplies."
PLATES = "Motor vehicle registration plates are issued in Kampala."


def _label(start: int) -> str:
    return f"FY{start}-{str(start + 1)[2:]}"


def _reranked(question: str, candidates: list[dict]) -> dict[str, dict]:
    retriever = object.__new__(HybridRetriever)
    retriever._reranker = _CpuLexicalReranker()
    retriever._rerank_logit_scale = None
    with mock.patch("app.config.flags.flags.is_enabled", side_effect=lambda name, *a, **k: name == "reranker"):
        retriever._rerank(question, candidates)
    return {c["id"]: c for c in candidates}


def test_scores_are_bounded_and_a_passage_with_no_shared_word_scores_zero() -> None:
    scores = _CpuLexicalReranker().predict([("What is the VAT rate?", VAT), ("What is the VAT rate?", PLATES), ("", VAT)])
    assert 0.0 < scores[0] <= 1.0
    assert scores[1] == 0.0
    assert scores[2] == 0.0


def test_a_stale_edition_is_demoted_below_the_current_one() -> None:
    current = fiscal_year_rank(current_fiscal_year())
    old = current - STALE_EDITION_YEARS - 1
    hits = _reranked(
        "What is the VAT rate?",
        [{"id": "stale", "text": VAT, "fiscal_year": _label(old)}, {"id": "current", "text": VAT, "fiscal_year": _label(current)}],
    )
    assert hits["stale"]["score_rerank"] < hits["current"]["score_rerank"]
    assert hits["stale"]["score_calibrated"] < hits["current"]["score_calibrated"]


def test_a_question_that_names_the_old_year_keeps_that_edition() -> None:
    current = fiscal_year_rank(current_fiscal_year())
    old = current - STALE_EDITION_YEARS - 1
    hits = _reranked(
        f"What was the VAT rate in {old}?",
        [{"id": "asked", "text": VAT, "fiscal_year": _label(old)}, {"id": "current", "text": VAT, "fiscal_year": _label(current)}],
    )
    assert hits["asked"]["score_rerank"] == hits["current"]["score_rerank"]


def test_with_no_dense_hits_the_gate_reads_the_overlap_score_not_a_squashed_zero() -> None:
    """Sparse-only search never reaches `_blend_cpu_lexical`, so `_rerank` must leave the scores set."""
    hits = _reranked("What is the VAT rate?", [{"id": "vat", "text": VAT}, {"id": "plates", "text": PLATES}])
    assert gating_relevance(hits["plates"]) == 0.0
    assert gating_relevance(hits["vat"]) == hits["vat"]["score_rerank"] > 0.0


@pytest.mark.parametrize(("rerank", "cpu", "installed"), [(True, True, True), (True, False, False), (False, True, False)])
def test_the_torch_free_image_gets_it_only_when_switched_on(monkeypatch: pytest.MonkeyPatch, rerank: bool, cpu: bool, installed: bool) -> None:
    monkeypatch.setattr(retriever_module, "RERANK_ENABLED", rerank)
    monkeypatch.setattr(retriever_module, "SPARSE_CPU_RERANKER", cpu)
    assert isinstance(_sparse_only_reranker(None), _CpuLexicalReranker) is installed
