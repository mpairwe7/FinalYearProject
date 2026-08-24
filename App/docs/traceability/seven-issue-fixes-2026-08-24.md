# Seven reported issues — fixes, and their verification on a live GPU stack

Traceability for the 2026-08-24 change set that answers seven issues raised
against the running assistant. Every fix is verified here against a real
deployment — local Docker on one pinned GPU, real Sunflower-14B-FP8 via vLLM,
real Qdrant, both Sunbird SALT speech tiers, reached over the public ngrok
tunnel rather than localhost, because that is the path the reports came from.

Companion docs: `docs/RAG_ARCHITECTURE.md` (answer language, withholding),
`docs/STAFF_AUTH_DEPLOYMENT.md` (RP-initiated logout),
`docs/API_REFERENCE.md` (`POST /v1/escalate`, SSE phases),
`docs/OPS_CONSOLE_REDESIGN.md` (the plain-language dashboard pass),
`App/docs/traceability/local-gpu-salt-ngrok-2026-08-22.md` (the stack itself).

## 1. The reports, and what each turned out to be

| # | Reported | Root cause |
|---|---|---|
| 1 | "Graphs may have to be more accurate and have understanding data that even non-IT personnel can understand" | The charts were accurate and accessible but captioned in the vocabulary of the system they monitor — `p95`, `retrieval_mode=hybrid`, URL paths on an axis, milliseconds, `abstention_precision`. Nothing a service manager can read. |
| 2 | "Answers are being returned in English and not being translated to the current language in use" | `run_chat_turn` — the streaming core behind **both** SSE and WebSocket, i.e. what the web client actually uses — localized against the `locale` **parameter**. Detection runs *inside* `generate_retrieval_only` and records its answer on the result, so a taxpayer who simply types Luganda arrives with `locale="en"`. `ChatModel.generate()` had been fixed for exactly this; its streaming twin never was. Two of its branches (agentic, empty-stream fallback) had no localization at all. |
| 3 | "Answers may need to be refactored since the model is still hallucinating" | The pipeline already **caught** the worst case — `entailment.numeric_contradiction` flags a rate the cited passage does not state, the judge escalates, a ticket is raised — and then printed the figure anyway. Separately, `SYSTEM_PROMPT` rule 10 told the model to "respond in the same language" unconditionally, which on an adapter-less deployment produces the documented repetition loop. |
| 4 | "Escalation to human agents. How do we do that. We can consider adding that" | The queue, officer console, SLA view, live arrivals stream and officer-reply delivery all existed. Every route into them was a judgement the **system** made; the taxpayer had none. |
| 5 | "Listening model takes quite a while to load and speak back. One has to press the button twice before they can even listen" | Two things. The mic button stayed on `idle` while the mic was opening, so people pressed again — and that second press called `start()` on an already-starting recognizer, which throws, and the throw was read as a failure that put the mic in `error`. And `SpeechModel`'s tiers are lazy below the model objects, so whoever asked first absorbed the cold path. |
| 6 | "Answers in another language take longer times, I think that can be done on better servers" | Not a server problem. One non-English turn translated the same question **twice** — the deterministic routers translate it before retrieval, and the hybrid retriever translates it again for the corpus — and nothing was memoised, so every repeat asked the model again. |
| 7 | "Signing in and out works well, but when I want to sign in as another user, it doesn't do that but just automatically signs me in the older account" | Sign-out cleared the token in localStorage and nothing else. The provider's session cookie survived, so the next `/authorize` was answered silently from it: no login screen, a fresh token, the same account. |

## 2. Stack under test

| Layer | Value |
|---|---|
| Date | 2026-08-24 |
| Host | shared 8× NVIDIA RTX A6000 48 GiB sandbox |
| Pinned GPU | **GPU 6** — the only card at 0% utilisation with 43 GiB free at selection time |
| `SUNFLOWER_GPU_MEM_UTIL` | **0.60** (down from the 0.70 default: the card already carries ~6 GiB of another tenant, and the previous run at 0.70 sat at ~92% full with ~4 GiB headroom) |
| Compose overlays | `docker-compose.yml` → `docker-compose.local-sunflower.yml` → `docker-compose.gpu-salt.yml` |
| LLM | local `App/Model/Sunflower-14B-FP8` via `vllm/vllm-openai:v0.8.5`, Marlin FP8 |
| Retrieval | local Qdrant, `dense_device=cuda:0`, `reranker_device=cuda:0` |
| Speech | Whisper-SALT + Spark-TTS-SALT, both `cuda:0` |
| MT | `RETRIEVAL_MT_BACKEND=local`, `REPLY_MT_BACKEND=local` — prompted MT through the already-loaded Sunflower |
| Tunnel | `https://struttingly-nongeological-briella.ngrok-free.dev` → `localhost:3032` |
| `RATE_LIMIT` | `10000/minute` — all tunnel traffic shares one slowapi bucket (see `docker-compose.gpu-salt.yml`), so the default 30/min measures the limiter, not the app |

Bring-up:

```bash
cd App
GPU_ID=6 SUNFLOWER_GPU_MEM_UTIL=0.60 \
NEXT_PUBLIC_OIDC_POST_LOGOUT_PATH= NEXT_PUBLIC_DEV_SIGNIN=true RATE_LIMIT=10000/minute \
docker compose -f docker-compose.yml -f docker-compose.local-sunflower.yml \
               -f docker-compose.gpu-salt.yml up -d --build
```

## 3. A configuration defect this verification found

The registered **Allowed Logout URLs** for this project are:

```
https://struttingly-nongeological-briella.ngrok-free.dev      <- bare origin, no path
https://landwind22-ura-chatbot.hf.space/signin                <- with /signin
```

The first cut of the RP-initiated logout hardcoded `OIDC_POST_LOGOUT_PATH = "/signin"`.
Auth0 matches Allowed Logout URLs **exactly**, so that build would have sent
`https://struttingly-…ngrok-free.dev/signin` — unregistered — and the provider
would have refused the logout and shown its own error page. The two deployments
are registered differently and one constant cannot serve both.

`NEXT_PUBLIC_OIDC_POST_LOGOUT_PATH` now carries it (default `/signin`, wired
through `App/frontend/Dockerfile` and `App/docker-compose.yml`), normalised so
`signin`, `/signin` and `/signin/` cannot produce three different URLs only one
of which is registered. The compose interpolation uses `${VAR-default}` and not
`${VAR:-default}`: an explicitly empty value is a real choice here — it means
"the origin, no path" — and `:-` would silently replace it with the default,
sending the unregistered URL.

Confirmed in the shipped bundle for this build:

```js
p = function () {
  let e = "".trim();                       // <- the inlined env value
  if (!e || "/" === e) return "";
  ...
}();
e.searchParams.set("post_logout_redirect_uri", `${window.location.origin}${p}`);
```

→ `https://struttingly-nongeological-briella.ngrok-free.dev`, which is registered.

## 4. Verified at commit time

The stack was brought up on GPU 6 and the following were confirmed against it.
The **functional through-the-tunnel run** (issues 2/3/4/5/6 exercised as live
requests) had not completed when this was committed — the API was still
loading Whisper-SALT onto the GPU — so it is recorded as outstanding below
rather than reported as a result.

### Confirmed in the running image, not just in the source tree

```
$ docker exec ura-app-api python -c "..."
mt module present, cache size: 512
figures_survived('VAT is 18%', 'VAT ebitundu 18 ku buli kikumi'): True
withhold flag: True
speech warmup: True ('en', 'lg', 'sw')
escalate route: ['/v1/escalate']
```

The `figures_survived` line is the one worth keeping: it is the cross-marker
case that a per-category comparison rejects. Luganda states a rate as "ebitundu
18 ku buli kikumi" — eighteen parts per hundred, no percent sign — so comparing
money and percentages separately sees the source's percentage vanish and the
translation grow an amount, and throws away a translation that is exactly right.

### Confirmed in the shipped frontend bundle

Extracted from `app-frontend:latest` and grepped, so this is what a browser
would actually download:

| Plain-language label | Present |
|---|---|
| `Answer speed`, `How long each thing takes`, `Where answers came from` | yes |
| `Found in URA documents`, `19 in 20 are faster`, `Running without a restart` | yes |
| `Talk to a URA officer`, `Opening the microphone` | yes |
| `Sign in as a different user`, `Translating the answer`, `Sticks to the documents` | yes |

| Jargon that had to go | Occurrences |
|---|---|
| `Chat p95 latency`, `Endpoint latency`, `Replica uptime`, `Average confidence` | **0** |

`endOidcSession` ships with `client_id` + `post_logout_redirect_uri` + `state`
and no `id_token_hint`, as §3 shows.

### The speech warm-up, observed doing its job

Issue 5's second half — "takes quite a while to load and speak back" — is the
lazy tier below `SpeechModel`'s model objects. The startup log shows the
warm-up thread pulling Spark-TTS-SALT onto the GPU *at boot*:

```
11:00:49,069 SpeechModel: Whisper-SALT ready
11:00:49,333 SpeechModel: MT translator ready (backend=prompted)
11:00:49,503 app.spark_tts_salt: Loading Spark-TTS-SALT 'Sunbird/spark-tts-salt' (device=cuda:0)
```

Nothing in the request path triggered that load — no client had connected yet.
Before `SPEECH_WARMUP`, the first taxpayer to press Listen paid for it. Note
also `device=cuda:0` on both tiers: the 2026-08-22 run accepted CPU fallback
here, and the cu128 pin in `Dockerfile.gpu` has since made the GPU path real.

### Infrastructure

| Check | Result |
|---|---|
| GPU 6 selected | only card at 0% utilisation, 43,131 MiB free |
| vLLM / Qdrant / Redis | healthy |
| Retrieval placement | `dense_device=cuda:0 reranker_device=cuda:0` |
| Corpus | 41 tags, 515 FAQ entries; BM25 state loaded |
| ngrok tunnel | `started tunnel … url=https://struttingly-nongeological-briella.ngrok-free.dev` |

### Functional run through the tunnel (2026-08-24, after the API finished loading Whisper-SALT)

`python3 scripts/verify_seven_issues.py` against
`https://struttingly-nongeological-briella.ngrok-free.dev` — 20 pass, 1 fail,
4 info. The fail is a harness comparison, not a figure-guard miss; see below.

| Check | Result |
|---|---|
| `/api/health`, `/api/v1/speech/health` | alive; asr=auto tts=auto mt=prompted |
| Issue 1 — plain-language dashboard labels in the shipped bundle | 6/6 present; `Chat p95 latency` / `Endpoint latency` / `Replica uptime` gone |
| Issue 2 — Luganda typed with no locale, `/v1/chat` | `locale=lg`, Luganda reply, 3.4s |
| Issue 2 — Swahili typed with no locale, `/v1/chat` | `locale=sw`, Swahili reply, 2.3s |
| Issue 2 — English stays English | `locale=en`, 0.5s |
| Issue 2 — streaming `/v1/chat/stream` | `metadata.locale=lg`, `translation.started` announced. The body was the Luganda abstention line, not the TIN-registration procedure the non-streaming path returned for the same question. Language is correct; content is a retrieval short-circuit. |
| Issue 3 — figure fidelity | **FAIL in the harness.** English `locale=en` answered the VAT threshold as UGX 300,000,000 (FY2026-27). The same English question with `locale=lg` abstained and served the Contact Centre numbers (0800 117 000 / 0800 217 000). Those are two independent generations, not a translation of one reply, so the digit scrape is not `figures_survived`. The guard itself was confirmed in the running image before this run (`figures_survived('VAT is 18%', 'VAT ebitundu 18 ku buli kikumi')` → True) and by `test_mt_cache.py`. |
| Issue 4 — `POST /v1/escalate` | 200, ticket created, second call reuses it, Luganda acknowledgement |
| Issue 5 — TTS after warm-up | en 1.0s `edge_tts` then 0.7s cached; lg 6.5s `spark_tts_salt` then 0.8s cached |
| Issue 6 — English vs Luganda chat | cold x18 (0.5s vs 9.2s), warm x5.36 (0.5s vs 2.8s). The memo closes the repeat; the first Luganda turn still pays for retrieval + prompted MT. |
| Issue 7 — RP-initiated logout | provider publishes `end_session_endpoint`; this build sends the registered bare origin; `/signin` carries "Sign in as a different user" |

### Still outstanding

1. **Perceptual verification** of Spark-TTS-SALT output — still open from
   2026-08-22, unchanged by this work.
2. **A real browser pass** over the redesigned dashboard. The sandbox cannot
   paint (see `docs/TEST_REPORT_2026-06-10.md`); the labels are asserted from
   the bundle and from `plainLanguage.test.tsx`, not from a screenshot.
3. **Streaming vs non-streaming retrieval.** The same Luganda TIN-registration
   question answered on `/v1/chat` and abstained on `/v1/chat/stream`. The
   language fix held (the abstention was in Luganda); the content did not.
   Not closed by this change set.
