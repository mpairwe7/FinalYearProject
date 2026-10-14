"""Low-bandwidth 2G/3G voice codec framing & caller drop-resume support (Phase 33, 2026).

Provides:
1. Pure-NumPy ITU-T G.711 mu-law 8 kHz <-> 16 kHz PCM16 transcoding (75% bandwidth
   reduction: 32 KB/s -> 8 KB/s) for 2G/EDGE/3G and Data Saver connections.
2. Adaptive low-bandwidth profile negotiation (including caption-first audio pause
   when RTT > 1500 ms or effectiveType == 'slow-2g').
3. HMAC-SHA256 caller drop-resume ticket minting and single-use verification within
   a configurable grace window (``RECEPTIONIST_CALLER_RECONNECT_GRACE_S``, default 45s).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass

import numpy as np

from ..flags import flags

logger = logging.getLogger(__name__)

_CONSUMED_RESUME_NONCES: dict[str, float] = {}
_NONCE_LOCK = threading.Lock()
_MAX_CONSUMED_NONCES = 2048


def get_caller_reconnect_grace_s() -> int:
    """Grace window (seconds) to hold a CallRoom when a mobile caller's socket drops."""
    try:
        return max(10, min(180, int(os.getenv("RECEPTIONIST_CALLER_RECONNECT_GRACE_S", "45"))))
    except ValueError:
        return 45


def _signing_material() -> bytes | None:
    raw = (os.getenv("RECEPTIONIST_RESUME_SECRET") or os.getenv("AUTH_DEV_SECRET") or "").strip()
    if not raw:
        return None
    return hashlib.sha256(f"ura-call-resume:{raw}".encode()).digest()


@dataclass(frozen=True)
class LowBandwidthProfile:
    """Negotiated transport adaptation for a constrained mobile voice connection."""

    enabled: bool = False
    codec: str = "pcm16_16k"  # "pcm16_16k" | "mulaw_8k" | "opus_12k"
    sample_rate: int = 16000
    caption_only: bool = False
    bitrate_kbps: int = 256


def negotiate_low_bandwidth_mode(
    config: dict[str, object] | None,
    *,
    subject: str = "",
) -> LowBandwidthProfile:
    """Negotiate low-bandwidth voice codec and caption-first degradation from client telemetry."""
    if not flags.is_enabled("low_bandwidth_voice_codec", subject=subject or None):
        return LowBandwidthProfile()

    cfg = config or {}
    eff_type = str(cfg.get("effective_type") or "").strip().lower()
    save_data = bool(cfg.get("save_data") or cfg.get("low_bandwidth"))
    try:
        rtt_ms = float(cfg.get("rtt_ms") or 0.0)
    except (TypeError, ValueError):
        rtt_ms = 0.0

    is_constrained = save_data or eff_type in ("slow-2g", "2g", "3g") or rtt_ms >= 600.0
    if not is_constrained:
        return LowBandwidthProfile()

    caption_only = bool(cfg.get("caption_only")) or eff_type == "slow-2g" or rtt_ms > 1500.0
    preferred_codec = str(cfg.get("audio_codec") or "mulaw_8k").strip().lower()
    if preferred_codec not in ("mulaw_8k", "opus_12k"):
        preferred_codec = "mulaw_8k"
    bitrate = 0 if caption_only else (12 if preferred_codec == "opus_12k" else 64)

    return LowBandwidthProfile(
        enabled=True,
        codec=preferred_codec,
        sample_rate=8000 if preferred_codec == "mulaw_8k" else 16000,
        caption_only=caption_only,
        bitrate_kbps=bitrate,
    )


def pcm16_16k_to_mulaw_8k(pcm16_bytes: bytes) -> bytes:
    """Downsample 16 kHz mono PCM16 to 8 kHz and encode as 8-bit ITU-T G.711 mu-law (4x smaller)."""
    if not pcm16_bytes or len(pcm16_bytes) < 4:
        return b""
    usable = len(pcm16_bytes) - (len(pcm16_bytes) % 2)
    samples_16k = np.frombuffer(pcm16_bytes[:usable], dtype=np.int16)
    samples_8k = samples_16k[::2].astype(np.float32) / 32768.0
    mu = 255.0
    mag = np.minimum(np.abs(samples_8k), 1.0)
    compressed = np.sign(samples_8k) * np.log1p(mu * mag) / np.log1p(mu)
    quantized = ((compressed + 1.0) * 0.5 * mu + 0.5).clip(0, 255).astype(np.uint8)
    return quantized.tobytes()


def mulaw_8k_to_pcm16_16k(mulaw_bytes: bytes) -> bytes:
    """Decode 8-bit 8 kHz G.711 mu-law and upsample (linear interpolation) to 16 kHz mono PCM16."""
    if not mulaw_bytes:
        return b""
    quantized = np.frombuffer(mulaw_bytes, dtype=np.uint8).astype(np.float32)
    mu = 255.0
    compressed = (quantized / mu) * 2.0 - 1.0
    samples_8k = np.sign(compressed) * (1.0 / mu) * (np.power(1.0 + mu, np.abs(compressed)) - 1.0)
    n = samples_8k.size
    if n == 1:
        samples_16k = np.repeat(samples_8k, 2)
    else:
        samples_16k = np.empty(n * 2, dtype=np.float32)
        samples_16k[0::2] = samples_8k
        samples_16k[1:-1:2] = 0.5 * (samples_8k[:-1] + samples_8k[1:])
        samples_16k[-1] = samples_8k[-1]
    pcm16 = (samples_16k * 32767.0).clip(-32768, 32767).astype(np.int16)
    return pcm16.tobytes()


def mint_call_resume_ticket(
    call_id: str,
    conversation_id: str,
    *,
    user_id: str = "",
    tenant_id: str = "default",
    ttl_seconds: int | None = None,
) -> str:
    """Mint an HMAC-signed, single-use caller resume ticket for dropped mobile voice sockets."""
    if not flags.is_enabled("call_drop_resume", subject=user_id or None):
        return ""
    key = _signing_material()
    if key is None:
        return ""
    ttl = ttl_seconds if ttl_seconds is not None else get_caller_reconnect_grace_s()
    payload = {
        "cid": call_id,
        "conv": conversation_id,
        "uid": user_id or "",
        "tid": tenant_id or "default",
        "nonce": uuid.uuid4().hex[:16],
        "exp": round(time.time() + max(5, ttl), 2),
    }
    raw_body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode("ascii")
    sig = hmac.new(key, raw_body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{raw_body}.{sig}"


def verify_call_resume_ticket(
    resume_ticket: str,
    *,
    expected_call_id: str = "",
    expected_user_id: str = "",
    expected_tenant_id: str = "default",
) -> dict[str, object] | None:
    """Verify and consume a single-use caller resume ticket; returns claims or ``None``."""
    if not resume_ticket or "." not in resume_ticket:
        return None
    key = _signing_material()
    if key is None:
        return None
    try:
        raw_body, sig = resume_ticket.rsplit(".", 1)
        raw_body_bytes = raw_body.encode("ascii")
        sig_ascii = sig.encode("ascii").decode("ascii")
        expected_sig = hmac.new(key, raw_body_bytes, hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig_ascii, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw_body_bytes).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        exp_ts = float(payload.get("exp") or 0.0)
    except Exception:
        return None
    if exp_ts <= time.time():
        return None
    if expected_call_id and payload.get("cid") != expected_call_id:
        return None
    if expected_user_id and payload.get("uid") != expected_user_id:
        return None
    if expected_tenant_id and payload.get("tid") != expected_tenant_id:
        return None
    nonce = str(payload.get("nonce") or "")
    if not nonce:
        return None
    if not flags.is_enabled("call_drop_resume", subject=str(payload.get("uid") or "") or None):
        return None
    with _NONCE_LOCK:
        now = time.time()
        if exp_ts <= now:
            return None
        for n, e in list(_CONSUMED_RESUME_NONCES.items()):
            if e <= now:
                del _CONSUMED_RESUME_NONCES[n]
        if nonce in _CONSUMED_RESUME_NONCES:
            return None
        if len(_CONSUMED_RESUME_NONCES) >= _MAX_CONSUMED_NONCES:
            oldest_nonce = min(_CONSUMED_RESUME_NONCES, key=_CONSUMED_RESUME_NONCES.__getitem__)
            del _CONSUMED_RESUME_NONCES[oldest_nonce]
        _CONSUMED_RESUME_NONCES[nonce] = exp_ts
    return payload
