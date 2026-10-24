# Chat voice mode — live check (ADR-0016)

How to watch one spoken turn go through the chat's voice mode against real
speech recognition, a real LLM and real TTS, and what it should look like.
The mocked browser specs (`App/frontend/e2e/voice*.spec.ts`) pin the dock's
behaviour. This check is for what they cannot see: real recognition, real
latencies, and the answer in the language that was heard.

## What a turn looks like

Entering voice mode opens the microphone at once. The dock in the composer's
place then shows, in order:

| Phase | Label (en) | What is true |
| --- | --- | --- |
| `listening` | Listening | The mic is open and nothing has been said yet. Teal orb. |
| `hearing` | Hearing you… | Speech started; the orb follows the voice. |
| `pausing` | Sending when you stop… | The pause is counting down round **Send now**. Talking again resets it. |
| `transcribing` | Got it | The turn ended, and the recording is on `POST /v1/asr?domain=tax`. |
| `thinking` | Thinking, then the answer's steps | *You said: "…"* is on screen, and the same words are already the taxpayer's turn, marked *Spoken*. `POST /v1/chat/stream` is answering in the language heard. |
| `preparing` | Getting ready to speak… | The answer has arrived, and `POST /v1/tts/stream` has been asked for it. |
| `speaking` | Speaking | The sentence being read is shown. **Interrupt and talk** stops the reading and opens the mic. |
| `listening` again | Listening | With auto-send on, the floor goes back to the taxpayer. |

Nothing is announced to screen readers while the microphone is open. *Got it*,
*Thinking* and a closed mic's notice are announced.

## Running it

1. **A stack with speech, an LLM and TTS.**
   - Use the GPU stack, or, when someone else is using it, an isolated one
     beside it.
   - The isolated stack needs unique service names, its own GPUs (4 and 7),
     a private `data_store` and `analytics` writable by uid 10001, Redis DBs
     8 and 9, and the shared Qdrant mounted read-only. Do not start
     `qdrant-indexer`.
   - The API's `/app/app` can come from the branch under test.
2. **The frontend, pointed at that API.**
   - Next bakes the rewrites at build time, so build with the URL:
     `INTERNAL_API_URL=http://127.0.0.1:<api port> bun run build`.
   - Then run `bun run next start -p <free port>`. Record the PID, and stop
     only that process afterwards.
3. **A spoken question for the microphone**, voiced by the stack's own TTS:

   ```bash
   python3 scripts/make_probe_mic_wav.py http://127.0.0.1:<api port> en \
     "What is the standard VAT rate in Uganda?" /tmp/q-en.wav
   python3 scripts/make_probe_mic_wav.py http://127.0.0.1:<api port> lg \
     "Omuwendo gwa VAT mu Uganda guli gutya kati?" /tmp/q-lg.wav
   ```

4. **The probe**, which uses `App/frontend`'s Playwright. `PW_SINGLE_PROCESS=1`
   is needed on the dev box, where Chromium only renders single-process.

   ```bash
   PW_SINGLE_PROCESS=1 node scripts/probe_voice_mode.mjs \
     --base http://localhost:<frontend port> --mic /tmp/q-en.wav --out /tmp/probe-en
   PW_SINGLE_PROCESS=1 node scripts/probe_voice_mode.mjs \
     --base http://localhost:<frontend port> --mic /tmp/q-lg.wav --out /tmp/probe-lg \
     --language Luganda --device phone
   ```

   - The probe writes a screenshot per phase and `timeline.json`, which
     records each phase with the dock's words and each request.
   - It exits 1 if a phase is skipped, if what was heard reached the screen
     after the answer, or if the answer is the stream's error text.
5. **Tear down.**
   - Run `docker compose -p <project> down`.
   - Check the GPUs in `nvidia-smi` are back where they were.
   - Run `FLUSHDB` on Redis DBs 8 and 9, after checking every key is the
     probe's own.
   - Delete the private data copies, which now hold the test conversations.

## Measured 2026-10-24

Isolated stack: Sunflower-Qwen3.5-9B on vLLM (GPU 7); the API with
SunflowerASR and Orpheus (GPU 4). Timings are in milliseconds from entering
voice mode, warm, on desktop.

| Step | ms |
| --- | --- |
| Mic open (`listening`) | 103 |
| Turn ended (`transcribing`), 1.2 s after the speech stopped | 5527 |
| What was heard on screen (`/v1/asr` took 0.37 s) | 5945 |
| Answer complete | 7361 |
| First audio (`speaking`) | 7991 |

- **Luganda on a phone:** the transcript was on screen 0.6 s after the turn
  ended (5.8 s in), and a correct answer (18%) followed 5.3 s later. Every label was in Luganda (*Nnoonya mu
  tterekero lya URA*, *Ogambye: "…"*, *Njogera*). The stream request carried
  `locale: lg` and `locale_explicit: true`.
- **Cold start:** the first turn after an API restart sat in *Searching the URA
  knowledge base* for 67 s while the retrieval models loaded. Before this
  change, the same wait had nothing on screen.
- **A mishearing, made visible.** Orpheus's "VAT rate" was heard as
  "vetereite". `/v1/asr`'s tax repair fixes TIN and URA, not VAT homophones
  (G259). The answer abstained, but the taxpayer could see why before it
  arrived.
- **Database errors:** "unable to open database file" in the API's logs means
  the private `data_store` or `analytics` is not writable by the container's
  `appuser` (uid 10001). Use a fresh, empty `data_store`: the app creates its
  databases, and no copy of real conversations is needed.
