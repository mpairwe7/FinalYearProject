"""Machine-readable marking of AI-generated media.

The assistant tells people they are talking to an AI (the chat UI and the
call greeting, "I'm your AI assistant"). Synthesised speech also leaves the
API as a file — the ``/v1/tts`` and ``/v1/voice/chat`` WAVs — and a file
carries no such sentence. This module marks it in the file itself, using the
IPTC Digital Source Type vocabulary that C2PA and the EU Code of Practice on
marking AI-generated content refer to (``trainedAlgorithmicMedia``):

* WAV gets a RIFF ``LIST/INFO`` chunk — ``ICMT`` (comment) states the audio
  is AI-generated, names the IPTC term and the generator; ``ISFT`` names the
  software. Players and ``ffprobe`` show it; audio samples are unchanged.

This is the metadata layer only. The Code of Practice also expects an
imperceptible watermark for synthetic audio; that needs a watermarking model
and is tracked as open work in the gap register, not claimed here.
"""

from __future__ import annotations

import os

#: IPTC Digital Source Type for media created by a trained model.
DIGITAL_SOURCE_TYPE = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"


def _info_subchunk(chunk_id: bytes, text: str) -> bytes:
    data = text.encode("utf-8") + b"\x00"
    if len(data) % 2:
        data += b"\x00"
    return chunk_id + len(data).to_bytes(4, "little") + data


def is_marked(audio: bytes) -> bool:
    """True when *audio* already carries the AI-generated marker."""
    head, _, _ = audio.partition(b"data")
    return DIGITAL_SOURCE_TYPE.encode("ascii") in head


def mark_wav(audio: bytes, *, generator: str) -> bytes:
    """Return *audio* with an AI-generated ``LIST/INFO`` chunk before its ``data`` chunk.

    Anything that is not a RIFF/WAVE file, or is already marked, is returned
    unchanged; so is a WAV whose chunk layout cannot be walked safely.
    """
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE" or is_marked(audio):
        return audio
    offset = 12
    while offset + 8 <= len(audio):
        chunk_id = audio[offset : offset + 4]
        size = int.from_bytes(audio[offset + 4 : offset + 8], "little")
        if chunk_id == b"data":
            break
        offset += 8 + size + (size % 2)
    else:
        return audio
    version = os.getenv("APP_VERSION", "")
    info = (
        b"INFO"
        + _info_subchunk(
            b"ICMT",
            f"AI-generated speech. IPTC digitalSourceType: {DIGITAL_SOURCE_TYPE}. Generator: {generator or 'unknown'}",
        )
        + _info_subchunk(b"ISFT", f"URA Chatbot {version}".strip())
    )
    list_chunk = b"LIST" + len(info).to_bytes(4, "little") + info
    marked = audio[:offset] + list_chunk + audio[offset:]
    return marked[:4] + (len(marked) - 8).to_bytes(4, "little") + marked[8:]
