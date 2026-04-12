"""Personal memory package (Phase 16).

Three-tier memory model per
``docs/URA_Chatbot_Roadmap_2026_Enhanced.md`` §5:

- **Working** (30 min TTL)     — current conversation, in-process/Redis
- **Episodic** (90 day TTL)    — summaries of past conversations
- **Semantic** (indefinite)    — extracted user facts with provenance,
                                 confidence score, and temporal decay

Every read is **consent-gated** on the ``personalization`` purpose
(UDPA 2019); every write records provenance (which conversation /
which extractor model).  Forgetting is a first-class operation
that cascades across all three tiers when the user withdraws
consent or triggers `DELETE /v1/me`.

Feature flag: ``FLAG_MEMORY_ENABLED`` default false.
"""

from __future__ import annotations

from .decay import compute_decayed_confidence, decay_factor
from .episodic import EpisodicMemory, EpisodicSummary
from .extractor import FactCandidate, FactExtractor
from .semantic import SemanticMemory, UserFact
from .service import MemoryService, get_memory_service
from .working import WorkingMemory

__all__ = [
    "EpisodicMemory",
    "EpisodicSummary",
    "FactCandidate",
    "FactExtractor",
    "MemoryService",
    "SemanticMemory",
    "UserFact",
    "WorkingMemory",
    "compute_decayed_confidence",
    "decay_factor",
    "get_memory_service",
]
