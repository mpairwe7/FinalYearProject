# Performance and robustness gaps, and the fixes that fit this stack

**Uganda Revenue Authority (URA) AI Taxpayer Assistant**
**Report date**: 2026-09-29
**Scope**: The local Docker GPU stack (Sunflower on vLLM, Whisper-SALT, Spark-TTS-SALT, Qdrant, Redis, one API worker) and the public ngrok hop. This is a gap register, not a new load test.
**Sources**: `docs/runbooks/capacity-slo.md` (measured 2026-08-19, re-benchmarked 2026-09-08), language-id and call-replay runs of 2026-09-24, the Orpheus FP8 eval of 2026-09-24, and the live container layout observed on this host.

---

## 1. What is already within budget

A cached FAQ, a calculator, or a guided workflow answers in tens to hundreds of milliseconds. The public cap on those paths is `RATE_LIMIT` (30 requests a minute per IP), not the GPU. Stream time-to-first-token on an idle vLLM is about 100 ms.

These items are already closed in the tree and are not open gaps:

- Speech health stays HTTP 200 and reports `degraded` when Orpheus is configured but down, and TTS skips the sidecar during cooldown.
- A follow-up amount stays on the last calculator instead of a handwritten PAYE, VAT, and withholding menu.
- A rate or a section number such as "VAT 18" or "Section 5" is no longer rewritten into a step list.
- A Gemini turn with no audio for 15 seconds can fall back to the local cascade. That only catches a hang. It does not shorten a normal 11–14 second first audio.

Leave `tool_use`, `hyde`, `tool_rag`, and `graph_fusion` off. Turning them on adds model calls. It does not remove the vLLM queue.

## 2. Ranked gaps and fixes

### 2.1 Hybrid generation misses 3 seconds as soon as a few people ask at once

Measured hybrid generation is about 2 seconds at the median and already past 3 seconds at p95 with 4 requests in flight. A cold turn that also hits Qdrant is about 7 seconds. One request may then sit for `LLM_DEADLINE_SECONDS` (45) and the whole provider chain for `LLM_TOTAL_BUDGET_SECONDS` (70). There is one uvicorn worker, so a handful of those calls blocks every new chat and every live call.

The comfortable envelope in the capacity runbook is about 3 short uncached generations per second before p95 crosses 3 seconds, and about 15.5 per second before the GPU token pipe flattens. At 32 in flight, generation p95 was about 6.4 seconds. At 64 in flight, p95 was about 11 seconds and p99 about 18 seconds. The load snippet in that runbook sets `--max-num-seqs 64` while the measured envelope used 32.

**Fix.** Cap in-flight hybrid generations at about 4 if the 3-second p95 is the promise. Past that, return the FAQ or calculator result, or a short streamed "still looking", instead of queueing into the 45-second wait. On vLLM, enable prefix caching and keep `--max-num-seqs` at 32. Split latency by `retrieval_mode`. A blended `/v1/chat` number hides a fast FAQ behind a slow generation. Publish 3 seconds as the hybrid-generation target. The 2-second figure in the docs is unmet for that path. k6 today does not cover `/v1/chat/stream`.

### 2.2 Luganda speech cannot hit a conversational time-to-first-audio

Orpheus, measured at about 234 ms to first audio (RTF 0.63) on the 2026-09-24 FP8 eval, is not serving. The `ura-app-orpheus-tts` container has been exited. Luganda then uses Spark-TTS-SALT: one shot, about 4–5 seconds, slower than real time, and capped at 8 seconds of speech. English and Kiswahili calls use Gemini Live. First audio on those turns is 11–14 seconds, and one Kiswahili caption reached about 33 seconds. Barge-in stops Gemini in about 0.7–1.1 seconds, which is acceptable. The 15-second stall switch does not make the normal turn faster.

**Fix.** Start the Orpheus sidecar for Luganda and Kiswahili and stream the first sentence instead of waiting for the whole clip. For English, stream Sunflower's first sentence into the local voice when Gemini has not produced audio within a few seconds, and keep Gemini only when it wins that race. Do not lower the 15-second cutoff to 1 second. That would abandon Gemini on every normal turn. English local voice remains edge-tts. Spark has no English speaker id.

### 2.3 One API process owns every live call

`WORKERS=1` is required. Call rooms and bridge sockets live in an in-memory dict. An API restart drops every call. A second worker would split those rooms, so barge-in and language state would disagree. Redis is already in the stack and already stores the calculator follow-up. It does not store the call.

**Fix.** Put call-room state in Redis with a short TTL. Keep the WebSocket on one process, and let a reconnect resume the same room. After that, a second worker is safe. Until then, do not raise `WORKERS`.

### 2.4 A wrong tax figure can still be spoken

Retrieval answers are not checked against the rate table unless the calculator regex already fired. The 2026-09-24 call replay scored 10 of 10 passes while a Luganda TIN question was answered as VAT registration and a Luganda VAT-rate question came back as a website and a phone number. Translation already refuses a reply whose digits changed (`figures_survived`). Generation does not do the same for a rate or a threshold. The August conversational MCP stress script is not evidence here: it called tools in-process with the model off, and it marked a painpoint resolved when the HTTP status was 200.

**Fix.** After a retrieval answer, extract percents and UGX amounts and check them against the rate table and the calculator tools. If they disagree, speak the tool result. Add those numeric checks to the call-replay scorer so a wrong 18% or a wrong registration threshold cannot pass.

### 2.5 Short and mixed-language speech still picks the wrong path

The 2026-09-24 language-id gate measured about 72% under 1.5 seconds and about 79% on mixed Luganda. Kiswahili at or above 1.5 seconds was about 89.7%. English-to-Luganda false switches were 0.0 after the gate, which is the result to keep. Smart Turn does not cover Luganda or Kiswahili, so end of turn is silence. The caller waits, then the wrong language path can run.

**Fix.** Keep the language hold on short audio, and decide the language from the conversation plus the new utterance rather than from one short clip. Tighten the silence timeout after a high-confidence Whisper endpoint. Do not block this on a new Luganda or Kiswahili turn detector.

### 2.6 The phone audio path has no jitter buffer and no echo control

The call socket is raw PCM at 16 kHz. There is no server echo cancellation. The public hop is ngrok. A jitter spike is not handled. Barge-in itself is in the acceptable range noted above.

**Fix.** Carry live call audio as Opus over WebRTC, with a jitter buffer and server-side echo cancellation. Leave batch `/v1/asr` and `/v1/tts` on PCM.

### 2.7 The capacity map no longer matches the machine

`docs/runbooks/capacity-slo.md` still places speech and Qdrant on GPU 4 and describes a two-GPU ceiling. On this host Sunflower is `ura-app-vllm-sunflower` on GPU 5, and Whisper-SALT plus Spark-TTS-SALT run in `ura-app-api` on GPU 2, on a shared 8-GPU machine. A deploy that follows the runbook can land on another tenant's card. Docker still probes `GET /health` only, so the API container stays healthy while speech status is `degraded` and Orpheus is in cooldown.

**Fix.** Rewrite the runbook to the current GPU map and the 32-sequence cap. Make `GET /v1/speech/health` part of the operator check: read `status`, not only HTTP 200.

## 3. Fix status

| Gap | Fix | State |
| --- | --- | --- |
| Hybrid p95 past 3s at 4 in flight | Cap in-flight hybrid work, prefix cache, `--max-num-seqs 32`, SLO by `retrieval_mode` | Open |
| Luganda first audio about 4–5s via Spark | Start Orpheus and stream the first sentence | Open. Sidecar is exited |
| English / Kiswahili first audio 11–14s | Local first sentence if Gemini has not spoken | Open. The 15s stall fallback only catches hangs |
| In-memory call rooms, one worker | Redis room state, then a second worker | Open |
| Retrieval figures unchecked | Check percents and UGX amounts against the rate table; score them in replay | Open |
| Short and mixed-language id | Hold short audio; use the conversation, not one clip | Open |
| Raw PCM over ngrok, no echo control | Opus, WebRTC, jitter buffer, server echo cancellation | Open |
| Stale GPU map and a green `/health` | Update the runbook; read speech `status` | Open |
| Orpheus down reported as ready | `status: degraded`, skip the sidecar while cooling down | Closed |
| Follow-up amount drops the calculator | Remember the last tool and replay it | Closed |
| Rates rewritten as steps | Step breaks only at a sentence boundary or a glued step number | Closed |

## 4. What this report does not claim

No new load test was run for this note. The latency figures are the capacity runbook and the September speech and call-replay measurements. The August MCP stress report's 100% scores are not used. They did not check the spoken figure, and they did not go through the model.
