"""Speak a question on a running api and pad it for Chromium's fake microphone.

    python3 scripts/make_probe_mic_wav.py <api_base> <language> "<question>" <out.wav>

The WAV is the microphone for ``scripts/probe_voice_mode.mjs``
(``docs/runbooks/chat-voice-mode-live-check.md``). The lead-in silence lets
voice mode's end-of-turn detector learn the room; the trailing silence is the
pause that ends the turn. Standard library only.
"""

import base64
import io
import json
import sys
import urllib.parse
import urllib.request
import wave

api, language, text, out = sys.argv[1:5]
# Only an http(s) api: urlopen would also open file: and other schemes.
if urllib.parse.urlparse(api).scheme not in ("http", "https"):
    raise SystemExit(f"api must be an http(s) URL, got {api!r}")
req = urllib.request.Request(  # nosec B310 # noqa: S310 — scheme checked above
    f"{api}/v1/tts",
    data=json.dumps({"text": text, "language": language}).encode(),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=180) as resp:  # nosec B310 # noqa: S310
    body = json.load(resp)
if body.get("error") or not body.get("audio_base64"):
    raise SystemExit(f"tts failed: backend={body.get('backend')} error={body.get('error')}")

with wave.open(io.BytesIO(base64.b64decode(body["audio_base64"])), "rb") as src:
    params = src.getparams()
    frames = src.readframes(src.getnframes())


def silence(seconds: float) -> bytes:
    """`seconds` of digital silence in the clip's own format."""
    return b"\x00" * (int(params.framerate * seconds) * params.sampwidth * params.nchannels)


with wave.open(out, "wb") as dst:
    dst.setparams(params)
    dst.writeframes(silence(1.2) + frames + silence(6.0))
print(json.dumps({
    "backend": body.get("backend"),
    "voice": body.get("voice"),
    "speech_s": round(len(frames) / (params.framerate * params.sampwidth * params.nchannels), 2),
    "rate": params.framerate,
}))
