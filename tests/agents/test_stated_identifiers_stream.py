"""A streamed turn keeps the TIN the taxpayer typed (G143, G154).

G143 exempted the identifiers a taxpayer states from output redaction, through a
turn-scoped ContextVar. The streaming core set it only inside the retrieval
thread, whose context copy never reaches the rest of the turn, so on the path
the web client uses a lookup the taxpayer asked for read "EFRIS holds … for TIN
[REDACTED_UG_TIN]". ``run_chat_turn`` now sets it in the request's own context,
which the turn's threads and the agentic stream inherit.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app import service as service_module
from app.guardrails import OutputGuard


def test_a_streamed_turn_keeps_the_tin_the_taxpayer_typed(monkeypatch) -> None:
    async def core(_model: Any, **_kwargs: Any):
        # The core redacts in worker threads and on the event loop alike.
        in_thread = await asyncio.to_thread(
            OutputGuard.redact_pii, "EFRIS holds this for TIN 1000000001; it also lists 1001234567."
        )
        yield ("token", in_thread)
        yield ("token", OutputGuard.redact_pii(" Again: 1000000001."))

    monkeypatch.setattr(service_module, "_run_chat_turn_events", core)

    async def collect() -> str:
        text = []
        async for event, payload in service_module.run_chat_turn(
            object(), message="What does EFRIS hold for TIN 1000000001?"
        ):
            if event == "token":
                text.append(payload)
        return "".join(text)

    text = asyncio.run(collect())

    assert text.count("1000000001") == 2
    # Only what the taxpayer typed is exempt: another TIN is still redacted.
    assert "1001234567" not in text
    assert "[REDACTED_UG_TIN]" in text
