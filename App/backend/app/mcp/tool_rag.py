"""Tool RAG — retrieve the top-k relevant tools per query.

2026 standard: at 15+ tools, pasting every schema into every
generation prompt wastes 3-5k tokens per turn and confuses the
model.  Instead we embed every tool description once and retrieve
the top-k most relevant per query, together with the mandatory
rails (escalate + knowledge-base search).

This module is intentionally **zero-dependency** — it uses a
simple token-overlap scorer so the feature works on any host
without sentence-transformers loaded.  When the dense model is
already available (Phase 1-13 retriever has one), callers can
inject it for semantic similarity scoring.

Feature flag: ``FLAG_TOOL_RAG`` default false — the agent falls
back to exposing all tools when off.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Tools that should ALWAYS be in the whitelist regardless of the
# retriever's opinion — these are the safety rails.
MANDATORY_RAILS = ["search_ura_knowledge_base", "escalate_to_human"]

_injected_dense: Any = None


def inject_dense_model(model: Any) -> None:
    """Share the retriever's already-loaded embedder. Never loads a model."""
    global _injected_dense
    _injected_dense = model


def injected_dense_model() -> Any:
    return _injected_dense


_MULTILINGUAL_TAX_SYNONYMS: dict[str, tuple[str, ...]] = {
    # Luganda
    "omusolo": ("tax",),
    "emisolo": ("tax", "taxes"),
    "okusasula": ("calculate", "pay"),
    "sasula": ("calculate", "pay"),
    "okubalirira": ("calculate",),
    "balirira": ("calculate",),
    "obupangisa": ("rental", "tax"),
    "omusaala": ("salary", "paye"),
    "emisaala": ("salary", "paye"),
    "omwalo": ("customs", "duty"),
    "ebyamagendo": ("customs",),
    "ebyamawanga": ("customs",),
    # Swahili
    "kodi": ("tax",),
    "ushuru": ("duty", "tax", "customs"),
    "forodha": ("customs", "duty"),
    "kulipa": ("calculate", "pay"),
    "lipa": ("calculate", "pay"),
    "kupiga": ("calculate",),
    "hesabu": ("calculate",),
    "mshahara": ("salary", "paye"),
    "mishahara": ("salary", "paye"),
    "mapato": ("income", "corporation"),
    "ongezeko": ("value", "added", "vat"),
    "zuio": ("withholding", "tax"),
    # Runyankole (nyn)
    "omushoro": ("tax",),
    "emishoro": ("tax", "taxes"),
    "okushashura": ("calculate", "pay"),
    "shashura": ("calculate", "pay"),
    "okubara": ("calculate",),
    "omushahara": ("salary", "paye"),
    "emishahara": ("salary", "paye"),
    "ebyensharo": ("customs", "duty"),
    # Acholi (ach)
    "mucoro": ("tax", "taxes"),
    "culu": ("calculate", "pay"),
    "kwano": ("calculate",),
    "mucara": ("salary", "paye"),
    "otwel": ("rental", "tax"),
    "biacara": ("corporation", "business", "tax", "trading", "license"),
    # 5-locale Cross-MDA / NTR / Licensing terms (Luganda, Swahili, Runyankole, Acholi)
    "pamiti": ("resolve", "mda", "ntr", "payment", "driving", "permit", "mowt", "udls"),
    "paasipooti": ("resolve", "mda", "ntr", "payment", "passport", "dcic"),
    "pasipoti": ("resolve", "mda", "ntr", "payment", "passport", "dcic"),
    "endagamuntu": ("assess", "life", "event", "onboarding", "national", "nira", "nin", "tin"),
    "ndagamuntu": ("assess", "life", "event", "onboarding", "national", "nira", "nin", "tin"),
    "kitambulisho": ("assess", "life", "event", "onboarding", "national", "nira", "nin", "tin"),
    "karaata": ("assess", "life", "event", "onboarding", "national", "nira", "nin", "tin"),
    "leseni": ("assess", "life", "event", "onboarding", "trading", "license", "kcca", "iras"),
    "layisensi": ("assess", "life", "event", "onboarding", "trading", "license", "kcca", "iras"),
    "ekyapa": ("resolve", "mda", "ntr", "payment", "land", "transfer", "mlhud", "stamp", "duty"),
    "cheti": ("evaluate", "b2g", "uesw", "clearance", "certificate", "tcc", "unbs"),
    "waraga": ("evaluate", "b2g", "uesw", "clearance", "permit", "certificate", "tcc"),
    # 5-locale Tax Relief / Voluntary Disclosure / Instalments / Account Recovery terms
    "ekisonyiwo": ("simulate", "tax", "relief", "instalments", "voluntary", "disclosure", "waiver"),
    "msamaha": ("simulate", "tax", "relief", "instalments", "voluntary", "disclosure", "waiver"),
    "okusaasirwa": ("simulate", "tax", "relief", "instalments", "voluntary", "disclosure", "waiver"),
    "kica": ("simulate", "tax", "relief", "instalments", "voluntary", "disclosure", "waiver"),
    "ebitundu": ("simulate", "tax", "relief", "instalments"),
    "awamu": ("simulate", "tax", "relief", "instalments"),
    "essimu": ("resolve", "efris", "account", "recovery", "lost", "sim", "tin"),
    # 5-locale Enterprise Connector & ToolAthlon Multi-App Synonyms (EFRIS, DTS, URSB, BWIMS, Payment)
    "risiti": ("efris", "fiscal", "invoice", "receipt"),
    "stakabadhi": ("efris", "fiscal", "invoice", "receipt"),
    "ankara": ("efris", "fiscal", "invoice"),
    "sitampu": ("dts", "digital", "stamp", "stamps", "excise"),
    "stempu": ("dts", "digital", "stamp", "stamps", "excise"),
    "kampuni": ("ursb", "business", "register", "verify", "corporation"),
    "kkampuni": ("ursb", "business", "register", "verify", "corporation"),
    "ebitongole": ("ursb", "business", "register", "verify"),
    "boondi": ("bwims", "bonded", "warehouse", "consignment", "inventory"),
}


def _tokenize(text: str) -> set[str]:
    raw_tokens = {t for t in re.findall(r"\w+", text.lower()) if len(t) > 2}
    expanded = set(raw_tokens)
    for t in raw_tokens:
        if t in _MULTILINGUAL_TAX_SYNONYMS:
            expanded.update(_MULTILINGUAL_TAX_SYNONYMS[t])
    return expanded


@dataclass
class ToolSelection:
    """Result of one Tool RAG selection pass."""

    tool_names: list[str]
    scores: dict[str, float] = field(default_factory=dict)
    fallback_reason: str = ""

    def __contains__(self, name: str) -> bool:
        return name in self.tool_names

    def __len__(self) -> int:
        return len(self.tool_names)


class ToolRAGSelector:
    """Select top-k tools for a query using token-overlap or dense embeddings.

    Algorithm:
    1. Tokenize the query
    2. For each eligible tool (post security-trimming), score the
       overlap between query tokens and
       (tool name + description + keywords).
    3. Return the top-k scored tools *plus* the mandatory rails.

    When ``dense_model`` is provided (a sentence-transformers model),
    scoring switches to cosine similarity of embedded tool
    descriptions vs the query embedding.  The token-overlap path is
    the always-available fallback.
    """

    def __init__(self, dense_model: Any = None) -> None:
        self._dense_model = dense_model if dense_model is not None else _injected_dense
        self._tool_embeds: dict[str, Any] | None = None

    def set_dense_model(self, model: Any) -> None:
        """Inject a sentence-transformers model at runtime."""
        self._dense_model = model
        self._tool_embeds = None  # invalidate cache

    def _score_token_overlap(self, query_tokens: set[str], tool) -> float:
        """Weighted token overlap score.

        Tool names are far more discriminating than descriptions (all
        tax tools share words like "tax", "uganda", "calculate") —
        a name match gets a 5x weight relative to a description match.
        That way "how much VAT" selects ``calculate_vat`` rather than
        tying with every other calculator.
        """
        schema = tool.schema
        name_tokens = _tokenize(schema.name.replace("_", " "))
        ns_tokens = _tokenize(schema.namespace.replace("_", " "))
        props = (schema.parameters or {}).get("properties") or {}
        param_tokens: set[str] = set()
        if isinstance(props, dict):
            for prop_key in props:
                param_tokens.update(_tokenize(str(prop_key).replace("_", " ")))
        desc_tokens = (_tokenize(schema.description) | ns_tokens | param_tokens) - name_tokens
        if not query_tokens:
            return 0.0
        name_hits = len(query_tokens & name_tokens)
        desc_hits = len(query_tokens & desc_tokens)
        if name_hits == 0 and desc_hits == 0:
            return 0.0
        # Name hits get a 5x weight, desc/param/namespace hits get 1x. Normalise by
        # query length so scores are comparable across tools.
        weighted = (name_hits * 5 + desc_hits) / max(len(query_tokens), 1)
        return round(weighted, 4)

    def _score_dense(self, query: str, tool) -> float:
        if self._dense_model is None:
            return 0.0
        import numpy as np

        try:
            if self._tool_embeds is None:
                self._tool_embeds = {}
            name = tool.schema.name
            if name not in self._tool_embeds:
                text = f"{tool.schema.name}: {tool.schema.description}"
                self._tool_embeds[name] = self._dense_model.encode(text, normalize_embeddings=True)
            tool_emb = self._tool_embeds[name]
            query_emb = self._dense_model.encode(query, normalize_embeddings=True)
            return float(np.dot(tool_emb, query_emb))
        except Exception:
            logger.debug("Tool RAG dense scoring failed", exc_info=True)
            return 0.0

    def select(
        self,
        query: str,
        eligible_names: list[str],
        k: int = 5,
        min_score: float = 0.0,
        *,
        diversify_namespaces: bool = True,
    ) -> ToolSelection:
        """Return the top-k tool names for *query*.

        *eligible_names* is the post-authz whitelist from
        :py:meth:`MCPClient.available_for`.  Mandatory rails are
        always appended (deduplicated) at the end.

        When *diversify_namespaces* is True (ToolAthlon multi-server mode),
        the highest-scoring tool from each strongly matched MCP namespace is
        represented before remaining *k* slots are filled with runners-up from
        already-represented namespaces.
        """
        from ..tools import ToolRegistry

        if not query or not eligible_names:
            return ToolSelection(tool_names=[], fallback_reason="empty inputs")

        query_tokens = _tokenize(query)
        scores: dict[str, float] = {}
        tool_namespaces: dict[str, str] = {}
        tool_has_name_hit: dict[str, bool] = {}
        use_dense = self._dense_model is not None

        for name in eligible_names:
            tool = ToolRegistry.get(name)
            if tool is None:
                continue
            if use_dense:
                s = self._score_dense(query, tool)
                has_name_hit = True
            else:
                s = self._score_token_overlap(query_tokens, tool)
                name_toks = _tokenize(tool.schema.name.replace("_", " ")) | _tokenize(
                    tool.schema.namespace.replace("_", " ")
                )
                has_name_hit = bool(query_tokens & name_toks)
            # Only record tools that actually matched — otherwise
            # a zero-score tool would prevent the fallback branch
            # from firing when the query has no relevant tools.
            if s > 0 and s >= min_score:
                scores[name] = round(s, 4)
                tool_namespaces[name] = tool.schema.namespace
                tool_has_name_hit[name] = has_name_hit

        ranked_all = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        if diversify_namespaces and ranked_all and k > 1:
            best_score = ranked_all[0][1]
            selected: list[str] = []
            seen_ns: set[str] = set()
            # Pass 1: top tool from each namespace with a direct name/namespace match
            for name, score in ranked_all:
                if len(selected) >= k:
                    break
                ns = tool_namespaces.get(name, "core")
                if ns not in seen_ns and tool_has_name_hit.get(name, False) and score >= best_score * 0.25:
                    selected.append(name)
                    seen_ns.add(ns)
            # Pass 2: fill remaining k slots by score order
            for name, _score in ranked_all:
                if len(selected) >= k:
                    break
                if name not in selected:
                    selected.append(name)
        else:
            selected = [name for name, _ in ranked_all[:k]]

        # Always add the mandatory rails (preserving order; dedupe)
        for rail in MANDATORY_RAILS:
            if rail in eligible_names and rail not in selected:
                selected.append(rail)

        if not selected:
            rails = [name for name in MANDATORY_RAILS if name in eligible_names]
            return ToolSelection(
                tool_names=rails or eligible_names[:k],
                scores=scores,
                fallback_reason="no tools scored; rails only",
            )

        return ToolSelection(tool_names=selected, scores=scores)
