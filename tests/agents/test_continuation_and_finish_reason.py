"""Unit tests for Phase E: finish_reason surfacing, bounded continuation, and truncation handling."""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest
from app import llm
from app.production_readiness import gap_gate_errors


@pytest.fixture(autouse=True)
def _reset_llm_meta():
    llm.clear_last_generation_meta()
    llm._vllm_limits.update(
        {"max_model_len": 0.0, "models_checked_at": 0.0, "tokenize_down_until": 0.0}
    )
    llm._vllm_limits_by_url.clear()
    yield
    llm.clear_last_generation_meta()


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_complete_with_continuation_length_then_stop_stitches_two_calls() -> None:
    """When vLLM returns finish_reason=='length' then 'stop', complete_with_continuation stitches the answer in 2 calls."""
    post_bodies: list[dict[str, Any]] = []
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Under Section 9 of the Income Tax Act, an individual is resident if they are present in Uganda for",
                    },
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 25},
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "183 days or more in any twelve-month period ending in the year of income [1].",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 145, "completion_tokens": 18},
        },
    ]

    class _FakeClient:
        def get(self, url: str, **_kw: Any) -> _FakeHttpResponse:
            return _FakeHttpResponse({"data": [{"id": "sunflower", "max_model_len": 8192}]})

        def post(self, url: str, content: bytes | None = None, json: dict[str, Any] | None = None, **_kw: Any) -> _FakeHttpResponse:
            if url.endswith("/tokenize"):
                return _FakeHttpResponse({"count": 100})
            body = json if json is not None else __import__("json").loads(content.decode("utf-8"))
            post_bodies.append(body)
            return _FakeHttpResponse(responses[len(post_bodies) - 1])

    with mock.patch.object(llm, "_get_vllm_client", return_value=_FakeClient()):
        gen = llm.complete_with_continuation(
            [{"role": "user", "content": "When is an individual resident in Uganda?"}],
            max_rounds=2,
        )

    assert len(post_bodies) == 2
    assert post_bodies[1].get("continue_final_message") is True
    assert post_bodies[1].get("add_generation_prompt") is False
    assert post_bodies[1]["messages"][-1]["role"] == "assistant"
    assert gen.finish_reason == "stop"
    assert gen.continuation_rounds == 1
    assert gen.text == (
        "Under Section 9 of the Income Tax Act, an individual is resident if they are present in Uganda for "
        "183 days or more in any twelve-month period ending in the year of income [1]."
    )
    assert gen.usage == {"prompt_tokens": 265, "completion_tokens": 43}


def test_complete_with_continuation_always_length_trims_at_sentence_boundary_and_appends_note() -> None:
    """When finish_reason remains 'length' after max_rounds, trims to a complete sentence and appends the note."""
    call_count = 0
    chunks = [
        "1. First statutory rule: PAYE is withheld monthly by the employer [1].\n",
        "2. Second statutory rule: VAT is charged at 18% on standard-rated supplies [2].\n",
        "3. Third statutory rule: Withholding tax on non-resident service contracts is 15% under Section 85 and the contract val",
    ]

    class _FakeClient:
        def get(self, url: str, **_kw: Any) -> _FakeHttpResponse:
            return _FakeHttpResponse({"data": [{"id": "sunflower", "max_model_len": 8192}]})

        def post(self, url: str, content: bytes | None = None, json: dict[str, Any] | None = None, **_kw: Any) -> _FakeHttpResponse:
            nonlocal call_count
            if url.endswith("/tokenize"):
                return _FakeHttpResponse({"count": 100})
            idx = min(call_count, len(chunks) - 1)
            call_count += 1
            return _FakeHttpResponse(
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": chunks[idx]},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )

    with mock.patch.object(llm, "_get_vllm_client", return_value=_FakeClient()):
        gen = llm.complete_with_continuation(
            [{"role": "user", "content": "List the rules."}],
            max_rounds=2,
        )

    assert call_count == 3  # 1 initial + 2 continuations
    assert gen.finish_reason == "length"
    assert gen.continuation_rounds == 2
    assert "contract val" not in gen.text
    assert "VAT is charged at 18% on standard-rated supplies [2]." in gen.text
    assert gen.text.endswith(llm.SHORTENED_CONTINUATION_NOTE)


def test_streaming_length_then_stop_yields_one_continuous_sse_answer() -> None:
    """_vllm_generate_stream continues seamlessly when round 1 ends with finish_reason=='length'."""
    stream_calls = 0

    class _FakeStreamContext:
        def __init__(self, lines: list[str]) -> None:
            self._lines = lines

        def __enter__(self) -> _FakeStreamContext:
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

        def iter_lines(self):
            return iter(self._lines)

    class _FakeStreamClient:
        def get(self, url: str, **_kw: Any) -> _FakeHttpResponse:
            return _FakeHttpResponse({"data": [{"id": "sunflower", "max_model_len": 8192}]})

        def post(self, url: str, json: dict[str, Any] | None = None, **_kw: Any) -> _FakeHttpResponse:
            return _FakeHttpResponse({"count": 80})

        def stream(self, method: str, url: str, content: bytes | None = None, **_kw: Any) -> _FakeStreamContext:
            nonlocal stream_calls
            stream_calls += 1
            if stream_calls == 1:
                return _FakeStreamContext(
                    [
                        'data: {"choices":[{"delta":{"content":"VAT standard rate is "},"finish_reason":null}]}',
                        'data: {"choices":[{"delta":{"content":"18 percent"},"finish_reason":"length"}]}',
                        "data: [DONE]",
                    ]
                )
            return _FakeStreamContext(
                [
                    'data: {"choices":[{"delta":{"content":" under the Value Added Tax Act [1]."},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ]
            )

    meta: dict[str, Any] = {}
    with mock.patch.object(llm, "_get_vllm_client", return_value=_FakeStreamClient()):
        tokens = list(
            llm._vllm_generate_stream(
                [{"role": "user", "content": "What is the VAT rate?"}],
                stream_meta=meta,
            )
        )

    full = "".join(tokens)
    assert stream_calls == 2
    assert full == "VAT standard rate is 18 percent under the Value Added Tax Act [1]."
    assert meta["finish_reason"] == "stop"
    assert meta["continuation_rounds"] == 1


def test_looks_truncated_is_not_consulted_when_finish_reason_exists_and_sovereign_never_calls_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_local_llm_then_cloud trusts finish_reason when present without calling _looks_truncated or _llm_cloud_fallback."""
    from app import service

    def _fake_generate(**_kwargs: Any) -> str:
        llm.set_last_generation_meta(
            {"finish_reason": "stop", "continuation_rounds": 0, "model": "Sunbird/Sunflower-Qwen3.5-9B"}
        )
        # Deliberately ends with a colon so _looks_truncated would have been tested if called
        return "Here is the summary:"

    with (
        mock.patch.object(service.llm_module, "generate", side_effect=_fake_generate),
        mock.patch.object(service, "_looks_truncated", side_effect=AssertionError("_looks_truncated must not be called")),
        mock.patch.object(service, "_llm_cloud_fallback", side_effect=AssertionError("cloud fallback must not be called")),
    ):
        reply = service._local_llm_then_cloud(
            query="Summarize VAT",
            passages=[{"text": "VAT is 18%", "source": "VAT Act"}],
            conversation_history=None,
            locale="en",
        )
    assert reply == "Here is the summary:"

    # Also verify Sovereign Mode never calls cloud even when finish_reason is None and reply has no punctuation
    monkeypatch.setenv("SOVEREIGN_MODE", "true")
    with (
        mock.patch.object(service.llm_module, "generate", return_value="Unpunctuated text without finish_reason"),
        mock.patch.object(service, "_llm_cloud_fallback", side_effect=AssertionError("cloud fallback forbidden in sovereign mode")),
    ):
        reply_sov = service._local_llm_then_cloud(
            query="Summarize VAT",
            passages=[{"text": "VAT is 18%", "source": "VAT Act"}],
            conversation_history=None,
            locale="en",
        )
    assert reply_sov == "Unpunctuated text without finish_reason"


def test_eval_debug_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVAL_DEBUG=1 is rejected by gap_gate_errors() when APP_ENV=production."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("EVAL_DEBUG", "1")
    errs = gap_gate_errors()
    assert any("EVAL_DEBUG" in e for e in errs)
