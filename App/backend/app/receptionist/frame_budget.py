"""What a call socket may send: frame size, frame rate, and a flood that does not stop (G304).

Neither the officer's audio socket nor the caller's call socket limited what
it took in. Every audio frame the officer sent was forwarded to the caller,
queued for the listeners and fed to the voice detector, so a client that sent
far more than a microphone does grew the utterance buffer without a byte
bound, and an odd-length frame made ``np.frombuffer`` raise and sent the
officer into the reconnect grace.

On a LiveKit call the audio is WebRTC, and the caller's, the officer's and a
listener's sockets carry control messages only; nothing limited those either
(ADR-0047). They take text through the same gate, and a binary frame there is
dropped (:meth:`FrameGate.unexpected`).

A :class:`FrameGate` belongs to one socket and is asked before a frame is
handed on. It drops a frame that is too big, malformed or over budget, counts
it in ``receptionist_frames_dropped_total`` and logs the flood once. It tells
the caller to close the socket only when frames have been dropped for
:data:`FLOOD_CLOSE_AFTER_S` seconds running. One dropped frame is never a
reason to close: that would put an officer into the reconnect grace and cut
the caller's call.

The limits are several times what the browser sends (PCM16 at 16 kHz is 640 B
every 20 ms: 50 frames/s, 32 KB/s; the script-processor fallback is 90 frames/s
at the same 32 KB/s; mu-law is 8 KB/s), so a tab that was throttled and then
releases a burst still passes.
"""

from __future__ import annotations

import contextlib
import enum
import logging
import time
from typing import TYPE_CHECKING, Any

from . import config, telemetry

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: A frame over this is dropped. A browser frame is 640 B.
MAX_FRAME_BYTES = 16 * 1024
#: A frame over this closes the socket at once (1009). Dropping it does not
#: bound what the peer sends: uvicorn has read up to 16 MiB of it already
#: (``ws_max_size``), and one a little over a second apart never adds up to a
#: flood. Nothing on a call socket comes near it (ADR-0047).
MAX_FRAME_HARD_BYTES = 64 * 1024
#: The frame budget: a refill rate and the burst it can hold.
FRAMES_PER_S = 200
FRAME_BURST = 200
#: The byte budget holds this many seconds of its rate (128 000 B at the default
#: 64 000 B/s, ``RECEPTIONIST_AUDIO_BYTES_PER_S``).
BYTE_BURST_S = 2
#: Text frames per window: the officer's console sends only ``hangup``, and on
#: a LiveKit call the caller's control messages are text too (ADR-0047).
TEXT_FRAMES = 15
TEXT_WINDOW_S = 10.0
#: Drops this long without a quiet second between them close the socket.
FLOOD_CLOSE_AFTER_S = 5.0
#: A flood ends when this long passes without a drop. Not "an accepted frame
#: ends it": a flood against a refilling budget still has frames accepted
#: between its drops.
FLOOD_QUIET_S = 1.0
#: Policy violation, with the reason the browser can read.
CLOSE_CODE = 1008
CLOSE_REASON = "too many frames"
#: Message too big (RFC 6455), for a frame over the hard cap.
TOO_LARGE_CODE = 1009
TOO_LARGE_REASON = "frame too large"

# Float arithmetic on a clock that has run for days leaves a refill a hair short.
_EPSILON = 1e-6


class Verdict(enum.Enum):
    PASS = enum.auto()  # hand the frame on
    DROP = enum.auto()  # counted; do not hand it on
    CLOSE = enum.auto()  # the flood has lasted: drop the frame and close the socket (once)


def _now() -> float:
    return time.monotonic()


class _Bucket:
    """A token bucket on a monotonic clock."""

    __slots__ = ("burst", "level", "rate", "stamp")

    def __init__(self, rate: float, burst: float, now: float) -> None:
        self.rate = rate
        self.burst = burst
        self.level = burst
        self.stamp = now

    def refill(self, now: float) -> None:
        if now > self.stamp:
            self.level = min(self.burst, self.level + (now - self.stamp) * self.rate)
            self.stamp = now

    def covers(self, amount: float) -> bool:
        return self.level + _EPSILON >= amount

    def spend(self, amount: float) -> None:
        self.level -= amount


class FrameGate:
    """One socket's budget. Not shared: a reconnecting officer gets a new one."""

    def __init__(self, side: str, *, call_id: str = "", clock: Callable[[], float] | None = None) -> None:
        self.side = side
        self.call_id = call_id
        self._clock = clock
        now = self._tick()
        bytes_per_s = config.get_audio_bytes_per_s()
        self._bytes = _Bucket(bytes_per_s, bytes_per_s * BYTE_BURST_S, now)
        self._frames = _Bucket(FRAMES_PER_S, FRAME_BURST, now)
        self._texts = _Bucket(TEXT_FRAMES / TEXT_WINDOW_S, TEXT_FRAMES, now)
        self._flood_since: float | None = None
        self._last_drop: float | None = None
        self._closed = False
        #: How :func:`close_flooded` closes the socket once a verdict is CLOSE.
        self.close_code = CLOSE_CODE
        self.close_reason = CLOSE_REASON

    def _tick(self) -> float:
        return (self._clock or _now)()

    def audio(self, data: bytes, *, one_byte_samples: bool = False) -> Verdict:
        """Whether to hand on a binary frame.

        PCM16 takes whole samples, so an odd length is malformed; mu-law is one
        byte a sample, so *one_byte_samples* lifts that rule and nothing else.
        """
        if self._closed:
            return Verdict.DROP
        now = self._tick()
        size = len(data)
        if size > MAX_FRAME_HARD_BYTES:
            return self._too_large(size)
        if size > MAX_FRAME_BYTES:
            return self._drop("size", now)
        if size % 2 and not one_byte_samples:
            return self._drop("malformed", now)
        self._frames.refill(now)
        self._bytes.refill(now)
        if not (self._frames.covers(1) and self._bytes.covers(size)):
            return self._drop("rate", now)
        self._frames.spend(1)
        self._bytes.spend(size)
        return Verdict.PASS

    def text(self, data: str) -> Verdict:
        """Whether to hand on a text frame: small, and at most 15 in 10 s.

        Its size is its UTF-8 bytes, as it came over the socket, not its
        characters: an emoji is four. A frame with more characters than the
        cap is over it whatever they are, so it is not encoded to find out.
        """
        if self._closed:
            return Verdict.DROP
        now = self._tick()
        size = len(data) if len(data) > MAX_FRAME_HARD_BYTES else len(data.encode("utf-8", "surrogatepass"))
        if size > MAX_FRAME_HARD_BYTES:
            return self._too_large(size)
        if size > MAX_FRAME_BYTES:
            return self._drop("size", now)
        self._texts.refill(now)
        if not self._texts.covers(1):
            return self._drop("rate", now)
        self._texts.spend(1)
        return Verdict.PASS

    def unexpected(self, data: bytes | str = b"") -> Verdict:
        """A frame this socket never takes: dropped, and counted as ``malformed``.

        A LiveKit call's control sockets carry text only (its audio goes over
        WebRTC), so a binary frame there is one. It is never handed on, and one
        never ends the call; a stream of them that lasts is a flood like any
        other (G304, ADR-0047). One over the hard cap closes the socket.
        """
        if self._closed:
            return Verdict.DROP
        size = len(data) if isinstance(data, bytes | bytearray) else len(str(data).encode("utf-8", "surrogatepass"))
        if size > MAX_FRAME_HARD_BYTES:
            return self._too_large(size)
        return self._drop("malformed", self._tick())

    def _too_large(self, size: int) -> Verdict:
        """A frame over the hard cap: counted, and the socket is closed with 1009 (once)."""
        telemetry.frame_dropped(self.side, "size")
        self._closed = True
        self.close_code = TOO_LARGE_CODE
        self.close_reason = TOO_LARGE_REASON
        logger.warning(
            "Closing the %s socket of call %s: a %d B frame is over the %d B cap",
            self.side,
            self.call_id or "?",
            size,
            MAX_FRAME_HARD_BYTES,
        )
        return Verdict.CLOSE

    def _drop(self, reason: str, now: float) -> Verdict:
        telemetry.frame_dropped(self.side, reason)
        if self._last_drop is None or now - self._last_drop > FLOOD_QUIET_S:
            # The first drop of a flood: start its clock, and say so once.
            self._flood_since = now
            logger.warning(
                "Dropping %s frames on call %s (%s); the socket is closed if this lasts %g s",
                self.side,
                self.call_id or "?",
                reason,
                FLOOD_CLOSE_AFTER_S,
            )
        self._last_drop = now
        if self._flood_since is not None and now - self._flood_since >= FLOOD_CLOSE_AFTER_S:
            self._closed = True
            logger.warning(
                "Closing the %s socket of call %s: its frames were dropped for %g s",
                self.side,
                self.call_id or "?",
                FLOOD_CLOSE_AFTER_S,
            )
            return Verdict.CLOSE
        return Verdict.DROP


async def close_flooded(websocket: Any, gate: FrameGate | None = None) -> None:
    """Close *websocket* as *gate* decided: 1008 for a flood, 1009 for a frame too large.

    Never raises: it may already be closing.
    """
    if websocket is None:
        return
    code = gate.close_code if gate is not None else CLOSE_CODE
    reason = gate.close_reason if gate is not None else CLOSE_REASON
    with contextlib.suppress(Exception):
        await websocket.close(code=code, reason=reason)
