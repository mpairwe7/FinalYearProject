# Live Docker GPU & Ngrok Regression Report — Phase 42 Portal Task Execution & Inbuilt DOM Co-Pilot

- **Date:** 2026-10-24
- **Branch / Commit Tested:** `dev` (`66ac6a1886` / `506054166a`)
- **Public Tunnel Endpoint:** `https://struttingly-nongeological-briella.ngrok-free.dev`
- **Local GPU Stack Profile:** `docker-compose.yml` + `docker-compose.local-retrieval.yml` + `docker-compose.local-sunflower.yml` + `docker-compose.gpu-salt.yml`
- **Overall Result:** **29 / 29 Live Regression Checks Passed (100%)**

---

## 1. Local GPU & HF Cache Infrastructure Topology

| Component | Container Name | Model / Storage | GPU / Port | Health |
| :--- | :--- | :--- | :--- | :--- |
| **Frontend SSR / UI** | `ura-app-frontend` | Next.js 16.4.1 (`/` + `/portal/[portalKey]`) | `:3032 -> :3000` | `healthy` |
| **FastAPI Agent Host** | `ura-app-api` | `BAAI/bge-m3`, `mxbai-rerank-base-v2`, `SunflowerASR-51-african-languages` | `GPU 2` (`:8083 -> :8000`) | `healthy` |
| **Primary LLM Engine** | `ura-app-vllm-sunflower` | `Sunbird/Sunflower-Qwen3.5-9B` (FP8, 262K context window) | `GPU 4` (`:8011 -> :8001`) | `healthy` |
| **Multilingual TTS** | `ura-app-orpheus-tts` | `Sunbird/orpheus-3b-tts-multilingual` (FP8 + SNAC 24 kHz) | `GPU 5` (`:18100 -> :8100`) | `healthy` |
| **Vector Store** | `ura-app-qdrant` | Qdrant v1.19.0 (`ura_knowledge_base_jsonl_active`) | `127.0.0.1:6333` | `healthy` |
| **Cache & Rate Store** | `ura-app-redis` | Redis 7.4-alpine (`allkeys-lru`) | `:6379` | `healthy` |

---

## 2. End-to-End Ngrok Regression Matrix

| Check ID | Scenario & Verification Target | Endpoint / Route | Result |
| :--- | :--- | :--- | :--- |
| **0a** | API liveness probe through Ngrok proxy | `GET /api/health` | `PASS (200 OK)` |
| **0b.1–0b.5** | SSR rendering of `/`, `/signin`, `/portal/prn_payments`, `/portal/tin_registration`, `/portal/motor_vehicle` | `GET /portal/*` | `PASS (200 OK)` |
| **1a** | Tier 1 autonomous TIN status verification (`tin_search_verify`, `live=false`) | `POST /api/v1/chat` | `PASS` |
| **1b** | Tier 1 autonomous PRN status verification (`payment_view_status`) in same session | `POST /api/v1/chat` | `PASS` |
| **1c** | Explicit TIN verification with purpose clause (`"verify TIN ... for my tax clearance"`) | `POST /api/v1/chat` | `PASS` |
| **2a** | Tier 2 partial PRN request elicits `inline_form` for missing `amount_ugx` | `POST /api/v1/chat` | `PASS` |
| **2b** | Multi-turn context remembering merges prior TIN with new `amount_ugx` into `confirmation_required` | `POST /api/v1/chat` | `PASS` |
| **2c** | Cross-session replay refusal (`ADR-0013` owner_key mismatch) | `POST /api/v1/portal/tasks/execute` | `PASS (403)` |
| **2d** | Legitimate owner `decision="allow"` executes task via `ToolRegistry.call(..., caller_confirmed=True)` | `POST /api/v1/portal/tasks/execute` | `PASS (200 OK)` |
| **2e** | Shared database idempotent replay returns identical PRN with `idempotent_replay=true` | `POST /api/v1/portal/tasks/execute` | `PASS` |
| **2f** | Sensitive 14-character NIN masked (`CM92*******23A`) on confirmation card with `raw_value` toggle | `POST /api/v1/chat` | `PASS` |
| **2g** | `decision="disallow"` cancels task and locks out subsequent `allow` replays (`400 Bad Request`) | `POST /api/v1/portal/tasks/execute` | `PASS` |
| **2h** | Mixed sales/payment prompt (`"Annual sales UGX 80000000; I need a payment slip for UGX 50000"`) selects `50000` | `POST /api/v1/chat` | `PASS` |
| **3a** | Anonymous Tier 3 request returns `step_up_login` card with `preserved_fields` and sanitized `returnTo` | `POST /api/v1/chat` | `PASS` |
| **3b** | Post-sign-in continuation (`resumed_after_login=true`) adopts session and presents confirmation card | `POST /api/v1/chat` | `PASS` |
| **3c** | TPCA Cap. 343 s. 75 Official Secrecy blocks cross-TIN obligation query (`card_type="access_denied"`) | `POST /api/v1/chat` | `PASS` |
| **4a** | Tier 4 statutory return filing (`TPCA s. 16`) returns `statutory_copilot_handoff` (`autofill=1`) | `POST /api/v1/chat` | `PASS` |
| **4b** | Stuck-user `portal_context` spotlights `#txtTIN` and scrubs `password`, `otp_code`, and `card_cvv` | `POST /api/v1/chat` | `PASS` |
| **5a–5b** | Long-horizon multi-turn Qdrant + `Sunflower-Qwen3.5-9B` RAG & session memory retention | `POST /api/v1/chat` | `PASS` |
| **LH 1–5** | 5-turn long-horizon journey (Presumptive tax + TIN memory -> Inline form excluding turnover -> Confirmation -> Idempotent execution -> Luganda `locale="lg"` card localization) | `POST /api/v1/chat` & `/execute` | `PASS` |
