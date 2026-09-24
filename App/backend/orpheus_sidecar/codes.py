"""Orpheus token stream → SNAC code frames, and the sliding decode window.

Pure Python (no torch, no vLLM) so the parsing that decides what audio comes
out is unit-tested without a GPU. Token ids and the prompt layout are from the
``Sunbird/orpheus-3b-tts-multilingual`` model card:

    prompt  = [START_OF_HUMAN] + tokenize("<speaker_id>: <text>") + [END_OF_TEXT, END_OF_HUMAN]
    output  = [START_OF_AI] [START_OF_SPEECH] audio... [END_OF_SPEECH] [END_OF_AI]

Audio arrives as 7 tokens per SNAC frame (~85 ms at 24 kHz): position ``p`` in
the frame carries a code offset by ``AUDIO_TOKEN_LO + p * 4096``. One frame
decodes to 2048 samples.

Streaming uses the window Canopy Labs' reference decoder uses: decode the last
four frames and keep only the second one's 2048 samples, so every emitted
sample was decoded with a frame of context on both sides. This module adds the
two ends the reference drops — the first frame (emitted from the first window)
and the last two (emitted from the final window) — so no speech is clipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

END_OF_TEXT = 128009
START_OF_SPEECH = 128257
END_OF_SPEECH = 128258
START_OF_HUMAN = 128259
END_OF_HUMAN = 128260
AUDIO_TOKEN_LO = 128266
CODEBOOK = 4096
FRAME_TOKENS = 7
AUDIO_TOKEN_HI = AUDIO_TOKEN_LO + FRAME_TOKENS * CODEBOOK
SAMPLES_PER_FRAME = 2048
SAMPLE_RATE = 24000
WINDOW_FRAMES = 4


def build_prompt_ids(text_ids: list[int]) -> list[int]:
    """Wrap an already-tokenised ``"<speaker>: <text>"`` in the training layout."""
    return [START_OF_HUMAN, *text_ids, END_OF_TEXT, END_OF_HUMAN]


def split_layers(frames: list[list[int]]) -> tuple[list[int], list[int], list[int]]:
    """SNAC's three code layers from de-offset 7-code frames (1 + 2 + 4 codes)."""
    l1: list[int] = []
    l2: list[int] = []
    l3: list[int] = []
    for f in frames:
        l1.append(f[0])
        l2.extend((f[1], f[4]))
        l3.extend((f[2], f[3], f[5], f[6]))
    return l1, l2, l3


@dataclass
class FrameAssembler:
    """Feed generated token ids; collect complete, de-offset SNAC frames.

    Text tokens and control tokens before ``START_OF_SPEECH`` are ignored; a
    token outside the codebook range for its frame position is dropped (the
    model occasionally emits one, and a mis-slotted code decodes as a click).
    """

    started: bool = False
    finished: bool = False
    frames: list[list[int]] = field(default_factory=list)
    _partial: list[int] = field(default_factory=list)

    def feed(self, token_id: int) -> bool:
        """Consume one token. True when it completed a new frame."""
        if self.finished:
            return False
        if token_id == START_OF_SPEECH:
            self.started = True
            self._partial.clear()
            return False
        if token_id == END_OF_SPEECH:
            self.finished = True
            return False
        if not self.started or not (AUDIO_TOKEN_LO <= token_id < AUDIO_TOKEN_HI):
            return False
        pos = len(self._partial)
        code = token_id - AUDIO_TOKEN_LO - pos * CODEBOOK
        if not 0 <= code < CODEBOOK:
            return False
        self._partial.append(code)
        if len(self._partial) == FRAME_TOKENS:
            self.frames.append(self._partial)
            self._partial = []
            return True
        return False


@dataclass(frozen=True)
class DecodeStep:
    """Decode ``frames[start:end]`` and emit samples ``[lo:hi]`` of the result."""

    start: int
    end: int
    lo: int
    hi: int | None


def next_steps(n_frames: int, emitted_through: int, final: bool) -> tuple[list[DecodeStep], int]:
    """Which windows to decode now, given ``n_frames`` complete frames.

    ``emitted_through`` is the number of frames whose samples are already out.
    Returns the steps and the new ``emitted_through``. Every frame is emitted
    exactly once, in order.
    """
    steps: list[DecodeStep] = []
    w = WINDOW_FRAMES
    f = SAMPLES_PER_FRAME
    if n_frames < w:
        # Too short for a full window: only the end of the utterance decodes it.
        if final and n_frames > emitted_through:
            steps.append(DecodeStep(0, n_frames, emitted_through * f, None))
            emitted_through = n_frames
        return steps, emitted_through
    if emitted_through == 0:
        # First window [0, 4): frames 0 and 1. Frame 0 never gets left
        # context, so waiting for any would only delay the first audio.
        steps.append(DecodeStep(0, w, 0, 2 * f))
        emitted_through = 2
    # Steady state: frame k comes from the window [k-1, k+3) — one frame of
    # context before it, two after — once frame k+2 exists.
    while emitted_through + 3 <= n_frames:
        k = emitted_through
        steps.append(DecodeStep(k - 1, k + w - 1, f, 2 * f))
        emitted_through += 1
    if final and emitted_through < n_frames:
        # The last one or two frames have no right context left to wait for.
        start = n_frames - w
        steps.append(DecodeStep(start, n_frames, (emitted_through - start) * f, None))
        emitted_through = n_frames
    return steps, emitted_through
