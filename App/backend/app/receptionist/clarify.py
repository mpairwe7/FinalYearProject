"""Pure-logic clarification gate and yes/no classifier for phone receptionist.

No Pipecat imports allowed in this module.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .lexicon import candidates

logger = logging.getLogger(__name__)

STOP_WORDS: frozenset[str] = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
    "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't",
    "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then",
    "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what",
    "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's",
    "whom", "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd",
    "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves",
})

FILLERS: frozenset[str] = frozenset({
    "um", "uh", "er", "ah", "like", "hmm", "well", "okay", "ok",
    "oh", "actually", "basically", "you know", "please",
})

_YES_RE = re.compile(
    r"\b(yes|yeah|yep|yup|correct|right|exactly|sure|that's it|indeed|positive|confirmed)\b",
    re.IGNORECASE,
)
_NO_RE = re.compile(
    r"\b(no|nope|not|wrong|incorrect|nah|negative|neither)\b",
    re.IGNORECASE,
)


def classify_yes_no(text: str) -> str:
    """Classify user response into 'yes', 'no', or 'neither'."""
    clean = text.strip()
    if not clean:
        return "neither"

    has_yes = bool(_YES_RE.search(clean))
    has_no = bool(_NO_RE.search(clean))

    if has_yes and not has_no:
        return "yes"
    if has_no and not has_yes:
        return "no"
    if has_yes and has_no:
        # Check which appears first
        yes_m = _YES_RE.search(clean)
        no_m = _NO_RE.search(clean)
        if yes_m and no_m:
            return "yes" if yes_m.start() < no_m.start() else "no"

    return "neither"


@dataclass
class ClarifyAction:
    action: str  # "none" | "ask_term" | "ask_repeat" | "ask_confirm_question" | "answer" | "restart" | "transfer"
    candidate: str | None = None
    target_word: str | None = None
    previous_word: str | None = None
    prompt: str | None = None
    corrected_text: str | None = None
    reason: str | None = None


@dataclass
class ClarifyState:
    stage: str  # "term" | "confirm" | "repeat"
    original_question: str
    target_word: str | None = None
    suggested_term: str | None = None
    previous_word: str | None = None
    attempts: int = 0


def _replace_word(text: str, target: str, replacement: str) -> str:
    """Safely replace *target* word with *replacement* in *text*."""
    pattern = re.compile(rf"\b{re.escape(target)}\b", re.IGNORECASE)
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.replace(target, replacement)


class ClarifyGate:
    """Evaluates STT confidence and manages clarification turns."""

    def __init__(self, threshold: float = 0.55, max_attempts: int = 2) -> None:
        self.threshold = threshold
        self.max_attempts = max_attempts

    def assess(
        self,
        text: str,
        words: list[Any] | None = None,
        threshold: float | None = None,
    ) -> ClarifyAction:
        """Assess caller utterance for low-confidence words requiring clarification."""
        th = threshold if threshold is not None else self.threshold

        # Case 1: words is None (fallback STT backend)
        if words is None:
            tokens = [w.strip(",.?!;:\"'()") for w in text.split()]
            for i, tok in enumerate(tokens):
                low_tok = tok.lower()
                if low_tok in STOP_WORDS or low_tok in FILLERS or len(tok) < 3:
                    continue
                prev_word = tokens[i - 1].lower() if i > 0 else None
                cands = candidates(tok, context=prev_word)
                if cands and cands[0][1] >= 0.7 and cands[0][0] != low_tok:
                    best_cand = cands[0][0]
                    return ClarifyAction(
                        action="ask_term",
                        candidate=best_cand,
                        target_word=tok,
                        previous_word=prev_word,
                        prompt=f"Excuse me, did you say {best_cand}?",
                    )
            return ClarifyAction(action="none")

        # Case 2: words with per-word probabilities provided
        content_items: list[tuple[int, Any]] = []
        for idx, item in enumerate(words):
            word_str = item.word if hasattr(item, "word") else item.get("word", "")
            w_clean = word_str.lower().strip(",.?!;:\"'()")
            if not w_clean or w_clean in STOP_WORDS or w_clean in FILLERS:
                continue
            if not any(c.isalpha() for c in w_clean):
                continue
            content_items.append((idx, item))

        if not content_items:
            return ClarifyAction(action="none")

        # Find content items below threshold
        low_items: list[tuple[int, Any]] = []
        for idx, item in content_items:
            prob = item.prob if hasattr(item, "prob") else item.get("prob", 1.0)
            if prob < th:
                low_items.append((idx, item))

        if not low_items:
            return ClarifyAction(action="none")

        # Pick lowest-probability content word
        lowest_idx, lowest_item = min(
            low_items,
            key=lambda pair: pair[1].prob if hasattr(pair[1], "prob") else pair[1].get("prob", 1.0),
        )
        target_str = lowest_item.word if hasattr(lowest_item, "word") else lowest_item.get("word", "")
        prev_str: str | None = None
        if lowest_idx > 0:
            p_item = words[lowest_idx - 1]
            prev_str = p_item.word if hasattr(p_item, "word") else p_item.get("word", "")
            prev_str = prev_str.strip(",.?!;:\"'()")

        cands = candidates(target_str, context=prev_str)
        if cands and cands[0][1] >= 0.7:
            cand_term = cands[0][0]
            return ClarifyAction(
                action="ask_term",
                candidate=cand_term,
                target_word=target_str,
                previous_word=prev_str,
                prompt=f"Excuse me, did you say {cand_term}?",
            )

        # No confident candidate match: check if mean prob is low
        probs = [
            it.prob if hasattr(it, "prob") else it.get("prob", 1.0)
            for it in words
            if hasattr(it, "prob") or "prob" in it
        ]
        mean_prob = sum(probs) / len(probs) if probs else 1.0

        if mean_prob < (th + 0.1):
            prompt = (
                f"Sorry, I didn't catch the word after '{prev_str}' — could you say it again?"
                if prev_str
                else "Sorry, I didn't catch that — could you say it again?"
            )
            return ClarifyAction(
                action="ask_repeat",
                target_word=target_str,
                previous_word=prev_str,
                prompt=prompt,
            )

        return ClarifyAction(action="none")

    def resolve(
        self,
        reply_text: str,
        words: list[Any] | None = None,
        state: ClarifyState | None = None,
        max_attempts: int | None = None,
    ) -> ClarifyAction:
        """Resolve a pending clarification turn given the caller's reply."""
        if state is None:
            return ClarifyAction(action="none")

        limit = max_attempts if max_attempts is not None else self.max_attempts
        decision = classify_yes_no(reply_text)
        reply_lower = reply_text.lower()

        # ------------------------------------------------------------------
        # Stage 1: "term" (AI asked: "Excuse me, did you say {term}?")
        # ------------------------------------------------------------------
        if state.stage == "term":
            sugg = (state.suggested_term or "").lower()
            confirmed = decision == "yes" or (sugg and sugg in reply_lower)

            if confirmed:
                corrected = _replace_word(
                    state.original_question,
                    state.target_word or "",
                    state.suggested_term or "",
                )
                state.stage = "confirm"
                state.original_question = corrected

                highlight = (
                    f"{state.previous_word} {state.suggested_term}"
                    if state.previous_word
                    else state.suggested_term
                )
                prompt = f"So if I got it right, you're asking about {highlight} — is that right?"
                return ClarifyAction(
                    action="ask_confirm_question",
                    candidate=state.suggested_term,
                    target_word=state.target_word,
                    corrected_text=corrected,
                    prompt=prompt,
                )

            if decision == "no":
                state.attempts += 1
                if state.attempts >= limit:
                    return ClarifyAction(action="transfer", reason="clarification_failed")
                return ClarifyAction(
                    action="restart",
                    prompt="Sorry about that — please tell me your question again.",
                )

            # Restatement / neither: caller spoke a replacement word
            cands = candidates(reply_text, context=state.previous_word)
            if cands and cands[0][1] >= 0.7:
                new_cand = cands[0][0]
                corrected = _replace_word(
                    state.original_question, state.target_word or "", new_cand
                )
                state.suggested_term = new_cand
                state.stage = "confirm"
                state.original_question = corrected

                highlight = (
                    f"{state.previous_word} {new_cand}"
                    if state.previous_word
                    else new_cand
                )
                prompt = f"So if I got it right, you're asking about {highlight} — is that right?"
                return ClarifyAction(
                    action="ask_confirm_question",
                    candidate=new_cand,
                    target_word=state.target_word,
                    corrected_text=corrected,
                    prompt=prompt,
                )

            state.attempts += 1
            if state.attempts >= limit:
                return ClarifyAction(action="transfer", reason="clarification_failed")
            return ClarifyAction(
                action="restart",
                prompt="Sorry about that — please tell me your question again.",
            )

        # ------------------------------------------------------------------
        # Stage 2: "repeat" (AI asked: "Sorry, I didn't catch the word...")
        # ------------------------------------------------------------------
        if state.stage == "repeat":
            cands = candidates(reply_text, context=state.previous_word)
            if cands and cands[0][1] >= 0.7:
                new_cand = cands[0][0]
                corrected = _replace_word(
                    state.original_question, state.target_word or "", new_cand
                )
                state.suggested_term = new_cand
                state.stage = "confirm"
                state.original_question = corrected

                highlight = (
                    f"{state.previous_word} {new_cand}"
                    if state.previous_word
                    else new_cand
                )
                prompt = f"So if I got it right, you're asking about {highlight} — is that right?"
                return ClarifyAction(
                    action="ask_confirm_question",
                    candidate=new_cand,
                    target_word=state.target_word,
                    corrected_text=corrected,
                    prompt=prompt,
                )

            state.attempts += 1
            if state.attempts >= limit:
                return ClarifyAction(action="transfer", reason="clarification_failed")
            return ClarifyAction(
                action="restart",
                prompt="Sorry about that — please tell me your question again.",
            )

        # ------------------------------------------------------------------
        # Stage 3: "confirm" (AI asked: "So if I got it right, you're asking about {X} — is that right?")
        # ------------------------------------------------------------------
        if state.stage == "confirm":
            if decision == "yes":
                return ClarifyAction(
                    action="answer",
                    corrected_text=state.original_question,
                )

            # Caller said "No" or neither
            state.attempts += 1
            if state.attempts >= limit:
                return ClarifyAction(action="transfer", reason="clarification_failed")
            return ClarifyAction(
                action="restart",
                prompt="Sorry about that — please tell me your question again.",
            )

        return ClarifyAction(action="none")
