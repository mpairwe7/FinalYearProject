"""Requests that must not reach a cloud model.

The call receptionist runs on local models only (decided 2026-09-30): a call
turn never reaches a hosted model (Gemini, Workers AI or Sunbird's API),
whatever this deployment configures for chat. The receptionist's own model
calls are local. Its English and Swahili turns, though, go through the chat
service, whose fallbacks (the LLM chain, the FAQ judge, the reply-translation
and speech translation tiers, the dense-retrieval fallback) can be switched on
by environment. Until G122 only the GPU stack's settings kept them off.

The scope travels with the request as a context variable, so those decision
points read it without every function taking a channel argument.
``asyncio.to_thread`` and the chat service's pools copy the context. A pool
that does not (``speech_service._cloud_call``) has its cloud tiers removed
before anything is submitted to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_LOCAL_ONLY: ContextVar[bool] = ContextVar("providers_local_only", default=False)


@contextmanager
def local_models_only(active: bool = True) -> Iterator[None]:
    """Within this block, cloud models are off (nested scopes never turn them back on)."""
    token = _LOCAL_ONLY.set(bool(active) or _LOCAL_ONLY.get())
    try:
        yield
    finally:
        _LOCAL_ONLY.reset(token)


def cloud_models_allowed() -> bool:
    """False inside :func:`local_models_only` (a call turn)."""
    return not _LOCAL_ONLY.get()
