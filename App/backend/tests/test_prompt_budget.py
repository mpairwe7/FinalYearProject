"""The vLLM prompt budget (G120).

The server's own numbers decide: the window it was started with
(``/v1/models``) and the prompt's exact token count (``/tokenize``). Over
budget, the oldest replayed exchanges go first, then the passages — never the
question or the instructions after it. The streamed answer, which had no budget
at all, goes through the same check.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

from app import llm


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeVllm:
    """Counts one token per two characters, like a Luganda-heavy prompt."""

    def __init__(self, window: int = 4096, tokenize_ok: bool = True) -> None:
        self.window = window
        self.tokenize_ok = tokenize_ok
        self.tokenize_calls = 0

    def get(self, url: str, **_kw: object) -> _FakeResponse:
        assert url.endswith("/models")
        return _FakeResponse({"data": [{"id": "sunflower", "max_model_len": self.window}]})

    def post(self, url: str, json: dict, **_kw: object) -> _FakeResponse:
        assert url.endswith("/tokenize") and "/v1/" not in url
        self.tokenize_calls += 1
        if not self.tokenize_ok:
            raise RuntimeError("no /tokenize on this server")
        chars = sum(len(m.get("content") or "") for m in json["messages"])
        return _FakeResponse({"count": chars // 2 + 10 * len(json["messages"])})


def _messages(history_pairs: int, passage_chars: int) -> list[dict[str, str]]:
    msgs = [{"role": "system", "content": "You are the URA assistant."}]
    for i in range(history_pairs):
        msgs.append({"role": "user", "content": f"old question {i} " + "x" * 600})
        msgs.append({"role": "assistant", "content": f"old answer {i} " + "y" * 600})
    final = "## Retrieved passages\n" + "p" * passage_chars + "\n\n## User question\nWhat is VAT?\n\n## Answer language\nWrite the answer in English."
    msgs.append({"role": "user", "content": final})
    return msgs


class PromptBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        llm._vllm_limits.update({"max_model_len": 0.0, "models_checked_at": 0.0, "tokenize_down_until": 0.0})

    def _fit(self, fake: _FakeVllm, msgs: list[dict[str, str]], max_tokens: int = 512) -> int:
        with mock.patch.object(llm, "_get_vllm_client", return_value=fake), \
             mock.patch.object(llm, "LLM_CONTEXT_WINDOW", 8192):
            return llm._fit_to_context(msgs, max_tokens)

    def test_served_window_beats_a_larger_configured_one(self) -> None:
        fake = _FakeVllm(window=4096)
        msgs = _messages(history_pairs=1, passage_chars=200)
        safe = self._fit(fake, msgs)
        self.assertEqual(safe, 512)
        self.assertEqual(llm._served_context_window(), 4096)

    def test_oldest_exchanges_go_first_in_whole_pairs(self) -> None:
        fake = _FakeVllm(window=4096)
        msgs = _messages(history_pairs=6, passage_chars=1000)
        self._fit(fake, msgs)
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles[0], "system")
        self.assertEqual(roles[-1], "user")
        self.assertEqual(len(roles) % 2, 0, roles)  # system + pairs + final user
        self.assertNotIn("old question 0", " ".join(m["content"] for m in msgs))
        self.assertIn("old question 5", " ".join(m["content"] for m in msgs))

    def test_passages_shrink_but_the_question_and_language_survive(self) -> None:
        fake = _FakeVllm(window=4096)
        msgs = _messages(history_pairs=0, passage_chars=20000)
        safe = self._fit(fake, msgs)
        final = msgs[-1]["content"]
        self.assertIn("## User question\nWhat is VAT?", final)
        self.assertTrue(final.endswith("Write the answer in English."))
        prompt_tokens = fake.post("/tokenize", json={"messages": msgs}).json()["count"]
        self.assertLessEqual(prompt_tokens + safe, 4096)

    def test_without_tokenize_the_estimate_is_conservative(self) -> None:
        fake = _FakeVllm(window=4096, tokenize_ok=False)
        msgs = _messages(history_pairs=6, passage_chars=6000)
        safe = self._fit(fake, msgs)
        self.assertGreater(llm._vllm_limits["tokenize_down_until"], 0)
        estimate = llm._estimate_prompt_tokens(msgs)
        self.assertLessEqual(estimate + safe, 4096)

    def test_streamed_answer_is_budgeted_too(self) -> None:
        captured: dict = {}

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def raise_for_status(self) -> None:
                return None

            def iter_lines(self):
                return iter(["data: [DONE]"])

        class _Client(_FakeVllm):
            def stream(self, method, url, content=None, **_kw):
                import json as _json

                captured["body"] = _json.loads(content)
                return _Stream()

        fake = _Client(window=4096)
        msgs = _messages(history_pairs=6, passage_chars=1000)
        with mock.patch.object(llm, "_get_vllm_client", return_value=fake), \
             mock.patch.object(llm, "LLM_CONTEXT_WINDOW", 8192):
            list(llm._vllm_generate_stream(msgs))
        body = captured["body"]
        prompt_tokens = fake.post("/tokenize", json={"messages": body["messages"]}).json()["count"]
        self.assertLessEqual(prompt_tokens + body["max_tokens"], 4096)


class ShortenPassagesTest(unittest.TestCase):
    def test_keeps_everything_from_the_question_on(self) -> None:
        content = "## Retrieved passages\n" + "a" * 1000 + "\n\n## User question\nQ?\n\n## Answer language\nEnglish."
        out = llm._shorten_passages(content, 0.1)
        self.assertTrue(out.endswith("## User question\nQ?\n\n## Answer language\nEnglish."))
        self.assertLess(len(out), len(content))


if __name__ == "__main__":
    unittest.main()
