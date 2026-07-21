#!/usr/bin/env bash
# Live speech smoke — verifies STT / TTS / MT / voice-chat against a deployed
# URA Chatbot (Crane Cloud "Cloud (Sunbird)" speech mode). Companion to
# live_smoke.sh, which covers only text chat.
#
#   BACKEND_URL=https://ura-chatbot-<hash>.renu-01.cranecloud.io \
#   bash scripts/live_speech_smoke.sh
#
# Asserts the stable invariants (HTTP 200, error=null, non-empty audio that
# decodes to valid WAV, language-appropriate transcripts) and REPORTS the
# serving backend per call (sunbird_cloud / edge_tts / ...). It does not
# hard-pin English TTS to one backend, which may legitimately be edge_tts or
# Sunbird depending on pod egress. No secrets are handled by this script.
set -euo pipefail

BACKEND_URL="${BACKEND_URL:-https://ura-chatbot-6318a1b5.renu-01.cranecloud.io}"
TIMEOUT_SECONDS="${SPEECH_SMOKE_TIMEOUT_SECONDS:-90}"

command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }

BACKEND_URL="$BACKEND_URL" TIMEOUT="$TIMEOUT_SECONDS" python3 - <<'PY'
import base64, io, json, os, sys, time, urllib.request, urllib.error, wave

BASE = os.environ["BACKEND_URL"].rstrip("/")
TO   = float(os.environ["TIMEOUT"])
EN_TEXT = "The standard VAT rate in Uganda is eighteen percent."
LG_TEXT = "Webale kujja. Osobola otya okuwandiisa TIN?"

fails = []
def ok(label, extra=""):   print(f"PASS  {label:<34} {extra}")
def bad(label, extra=""):  print(f"FAIL  {label:<34} {extra}"); fails.append(label)

def req(method, path, *, body=None, raw=None, query="", headers=None):
    url = BASE + path + (("?" + query) if query else "")
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = dict(headers or {})
    if body is not None: h.setdefault("Content-Type", "application/json")
    if raw  is not None: h.setdefault("Content-Type", "application/octet-stream")
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=TO) as resp:
            return resp.status, json.load(resp), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        try: payload = json.loads(e.read() or b"{}")
        except Exception: payload = {}
        return e.code, payload, time.perf_counter() - t0
    except Exception as e:
        return None, {"_err": f"{type(e).__name__}: {e}"}, time.perf_counter() - t0

def audio_facts(b64):
    """Return (raw_bytes, fmt, sample_rate, duration_s). Handles WAV (Sunbird/
    Piper) and MP3 (edge_tts neural voices). Raises on anything unrecognized."""
    raw = base64.b64decode(b64)
    if raw[:4] == b"RIFF":
        w = wave.open(io.BytesIO(raw), "rb")
        return raw, "wav", w.getframerate(), w.getnframes() / max(1, w.getframerate())
    if raw[:3] == b"ID3" or raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return raw, "mp3", None, None       # edge_tts MP3; sample_rate comes from the response
    raise ValueError("unrecognized audio (not WAV or MP3)")

# 1) speech health
s, d, _ = req("GET", "/v1/speech/health")
if s == 200 and d.get("enabled") is True and d.get("status") == "ready":
    ok("speech/health", f"asr={d.get('asr_backend')} tts={d.get('tts_backend')} mt={d.get('mt_backend')}")
else:
    bad("speech/health", f"HTTP {s} {d}")
    print("\nSpeech is not enabled/ready — aborting (set SPEECH_ENABLED=true + FLAG_VOICE_CONSENT=true and redeploy).")
    sys.exit(1)

consent = {"X-Voice-Consent": "true"}
en_wav = lg_wav = None
en_sr = lg_sr = 16000

# 2) TTS English (edge_tts → MP3; Sunbird → WAV)
s, d, dt = req("POST", "/v1/tts", body={"text": EN_TEXT, "language": "en"})
if s == 200 and not d.get("error") and d.get("audio_base64"):
    try:
        en_wav, fmt, sr, dur = audio_facts(d["audio_base64"]); en_sr = sr or d.get("sample_rate") or 24000
        ok("tts:en", f"backend={d.get('backend')} fmt={fmt} {en_sr}Hz {(f'{dur:.2f}s' if dur else '')} {dt:.1f}s")
    except Exception as e:
        bad("tts:en", f"audio undecodable: {e}")
else:
    bad("tts:en", f"HTTP {s} backend={d.get('backend')} error={d.get('error')}")

# 3) TTS Luganda (Sunbird native speaker 248 → WAV)
s, d, dt = req("POST", "/v1/tts", body={"text": LG_TEXT, "language": "lg"})
if s == 200 and not d.get("error") and d.get("audio_base64"):
    try:
        lg_wav, fmt, sr, dur = audio_facts(d["audio_base64"]); lg_sr = sr or d.get("sample_rate") or 16000
        ok("tts:lg", f"backend={d.get('backend')} fmt={fmt} {lg_sr}Hz {(f'{dur:.2f}s' if dur else '')} {dt:.1f}s")
    except Exception as e:
        bad("tts:lg", f"audio undecodable: {e}")
else:
    bad("tts:lg", f"HTTP {s} backend={d.get('backend')} error={d.get('error')}")

# 4) STT English (round-trip on the synthesized clip)
if en_wav:
    s, d, dt = req("POST", "/v1/asr", raw=en_wav, query=f"sample_rate={en_sr}&language=en", headers=consent)
    txt = (d.get("text") or "").lower()
    if s == 200 and not d.get("error") and txt:
        hit = any(k in txt for k in ("vat", "percent", "eighteen", "18", "uganda"))
        (ok if hit else bad)("stt:en (round-trip)", f"backend={d.get('backend')} text={d.get('text')!r} kw={hit}")
    else:
        bad("stt:en (round-trip)", f"HTTP {s} backend={d.get('backend')} error={d.get('error')}")
else:
    bad("stt:en (round-trip)", "no English WAV from TTS step")

# 5) STT Luganda (round-trip)
if lg_wav:
    s, d, dt = req("POST", "/v1/asr", raw=lg_wav, query=f"sample_rate={lg_sr}&language=lg", headers=consent)
    if s == 200 and not d.get("error") and (d.get("text") or "").strip():
        ok("stt:lg (round-trip)", f"backend={d.get('backend')} text={d.get('text')!r}")
    else:
        bad("stt:lg (round-trip)", f"HTTP {s} backend={d.get('backend')} error={d.get('error')}")
else:
    bad("stt:lg (round-trip)", "no Luganda WAV from TTS step")

# 6) Translation both directions
for src, tgt, label in (("en", "lg", "translate:en->lg"), ("lg", "en", "translate:lg->en")):
    text = EN_TEXT if src == "en" else LG_TEXT
    s, d, dt = req("POST", "/v1/translate", body={"text": text, "source_lang": src, "target_lang": tgt})
    if s == 200 and not d.get("error") and (d.get("text") or "").strip():
        ok(label, f"backend={d.get('backend')} -> {d.get('text')!r}")
    else:
        bad(label, f"HTTP {s} backend={d.get('backend')} error={d.get('error')}")

# 7) Full voice pipeline (audio -> ASR -> [MT] -> LLM -> [MT] -> TTS -> audio)
for lang, wav, sr in (("en", en_wav, en_sr), ("lg", lg_wav, lg_sr)):
    if not wav:
        bad(f"voice/chat:{lang}", "no input WAV"); continue
    q = f"language={lang}&sample_rate={sr}&tts_enabled=true&top_k=4"
    s, d, dt = req("POST", "/v1/voice/chat", raw=wav, query=q, headers=consent)
    if s == 200 and not d.get("error") and d.get("reply") and d.get("reply_audio_base64"):
        ok(f"voice/chat:{lang}",
           f"asr={d.get('asr_backend')} mt={d.get('mt_backend') or '-'} tts={d.get('tts_backend')} "
           f"{dt:.1f}s | transcript={d.get('transcript')!r}")
    else:
        bad(f"voice/chat:{lang}", f"HTTP {s} error={d.get('error')} reply={bool(d.get('reply'))}")

print()
print(f"{'ALL SPEECH CHECKS PASSED' if not fails else str(len(fails)) + ' CHECK(S) FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
PY
