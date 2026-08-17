"""Hypothetical Document Embeddings (HyDE) for the dense retrieval leg.

Gao et al., 2022: embed a *hypothetical answer* instead of the raw
question so the dense vector sits closer to the passages that would
answer it. BM25 and the cross-encoder stay on the user's words —
HyDE is a dense-only transform.

Default is **off** (``FLAG_HYDE``). Leave that env unset and use
``FLAG_HYDE_PERCENT`` for a canary — an explicit ``FLAG_HYDE=false``
forces everyone off and ignores the percent. When on, the zero-latency
path is a domain template (no LLM). Set ``HYDE_LLM=true`` to spend one
short generation and fall back to the template if the model is
unavailable or returns nothing.

This module must stay import-cheap: the LLM path is deferred until a
flagged request actually needs it.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

HYDE_LLM = os.getenv("HYDE_LLM", "false").lower() in ("1", "true", "yes", "on")
HYDE_MAX_CHARS = int(os.getenv("HYDE_MAX_CHARS", "480"))

_TEMPLATE = (
    "Uganda Revenue Authority official guidance. "
    "This passage answers the taxpayer question: {query} "
    "It states the applicable rate or procedure, the legal basis, "
    "and the fiscal year in force."
)


def template_hypothetical(query: str) -> str:
    """Deterministic HyDE document — no model, stable for tests and CI."""
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return ""
    return _TEMPLATE.format(query=q)


def _llm_hypothetical(query: str) -> str:
    """One short vLLM completion. Empty string means 'use the template'.

    Local HF ``generate()`` is the full RAG answerer (passages, system
    prompt, 512 tokens). Calling it here would add a second 8B forward
    pass to every retrieval. HyDE-from-LLM is therefore vLLM-only, where
    a short ``/chat/completions`` call is already the serving path.
    """
    try:
        from . import llm as llm_module

        if getattr(llm_module, "LLM_BACKEND", "") != "vllm":
            return ""
        if not llm_module.is_available():
            return ""
        messages = [
            {
                "role": "system",
                "content": (
                    "Write one short Uganda Revenue Authority guidance paragraph "
                    "that would answer the user. No greeting. No disclaimer. "
                    "Do not invent a specific figure unless the question states it."
                ),
            },
            {"role": "user", "content": query},
        ]
        text = (llm_module._vllm_generate(messages) or "").strip()
    except Exception:
        logger.debug("HyDE LLM generation failed; using template", exc_info=True)
        return ""
    text = re.sub(r"\s+", " ", text)
    if len(text) < 20:
        return ""
    return text[:HYDE_MAX_CHARS]


def dense_query_text(query: str, *, subject: str | None = None) -> str:
    """Text to embed for the dense / Vectorize leg.

    Returns *query* unchanged when HyDE is off so existing embeddings,
    caches, and tests keep their exact vectors. *subject* is the user
    id the ``FLAG_HYDE_PERCENT`` bucket is keyed on; omit it and a
    percentage rollout falls through to the registry default (off).
    """
    from .flags import flags

    q = (query or "").strip()
    if not q or not flags.is_enabled("hyde", subject=subject):
        return q
    if HYDE_LLM:
        generated = _llm_hypothetical(q)
        if generated:
            logger.info("HyDE LLM document used (%d chars)", len(generated))
            return generated
    document = template_hypothetical(q)
    logger.debug("HyDE template document used")
    return document
