# `/v2/chat/stream` — WebSocket Chat Protocol

**Status:** Phase 0 (lifecycle + protocol negotiation only).
**Feature flag:** `ws_chat` (default off).
**Backward compat:** SSE at `/v1/chat/stream` is unchanged and stays the
default text-chat transport until at least Phase 6.

The wire shape mirrors OpenAI's `Responses API` WebSocket mode so a
future migration to a hosted model is a transport swap, not a UI
rewrite. URA-specific extensions are namespaced (`grounding`,
`workflow.*`, `confirmation_required`).

---

## Connection lifecycle

```
client                                        server
  │                                              │
  │  WebSocket upgrade (Authorization: Bearer)   │
  ├─────────────────────────────────────────────▶│  flag + auth check
  │                                              │
  │  session_start                               │
  ├─────────────────────────────────────────────▶│  validate
  │                              session_ready   │
  │◀─────────────────────────────────────────────┤
  │                                              │
  │  response.create                             │
  ├─────────────────────────────────────────────▶│  agentic pipeline
  │       response.metadata                      │
  │◀─────────────────────────────────────────────┤
  │       response.retrieval.completed           │  (Phase 2+)
  │◀─────────────────────────────────────────────┤
  │       response.tool_call.started             │  (Phase 2+)
  │◀─────────────────────────────────────────────┤
  │       response.tool_call.completed           │  (Phase 2+)
  │◀─────────────────────────────────────────────┤
  │       response.token (xN)                    │
  │◀─────────────────────────────────────────────┤
  │       response.grounding                     │
  │◀─────────────────────────────────────────────┤
  │       response.done                          │
  │◀─────────────────────────────────────────────┤
  │                                              │
  │  ping              (any time)                │
  ├─────────────────────────────────────────────▶│
  │              pong                            │
  │◀─────────────────────────────────────────────┤
  │                                              │
  │  session_end                                 │
  ├─────────────────────────────────────────────▶│  close 1000
  │                                              ◀
```

Max session duration: **60 min** (matches OpenAI WS cap). Server emits
`{"type": "session.expired"}` then closes.

---

## Client → server frames

### `session_start`

Sent immediately after the WS upgrade. Anything else as the first frame
results in `response.error` (`recoverable=false`) and a close.

| Field                  | Type    | Required | Notes |
|------------------------|---------|----------|-------|
| `type`                 | string  | yes      | `"session_start"` |
| `conversation_id`      | string  | no       | Echo from a prior turn. Empty starts a fresh conversation. |
| `previous_response_id` | string  | no       | Phase 3: opt-in resume. Phase 0 ignores it. |
| `locale`               | string  | no       | BCP47-ish, default `"en"`. |
| `protocol_version`     | int     | no       | Default `1`. |
| `tenant_id`            | string  | no       | Anonymous only; logged-in users get tenant from JWT. |

### `response.create`

Starts an agent turn.

```json
{
  "type": "response.create",
  "input": "How do I file VAT for July?",
  "tools": ["calculate_vat", "calendar"],
  "top_k": 4,
  "metadata": {"client_request_id": "..."}
}
```

| Field                  | Type     | Required | Notes |
|------------------------|----------|----------|-------|
| `input`                | string   | yes      | User message. |
| `tools`                | string[] | no       | Allow-list of tool names. Empty/missing → all tools the user's role grants. |
| `top_k`                | int      | no       | Retrieval depth, clamped 1–10. |
| `metadata`             | object   | no       | Echoed in `response.metadata`. |

**One in-flight `response.create` per socket.** Send the next only after
`response.done` / `response.error` / `response.cancelled`.

### `response.cancel`

Cancels the in-flight response. Server replies with `response.cancelled`.

### `tool_call.confirm` *(Phase 4)*

```json
{
  "type": "tool_call.confirm",
  "confirm_token": "...",
  "idempotency_key": "...",
  "decision": "approve"
}
```

### `session_end` / `ping`

`session_end` → server closes with code 1000.
`ping` → server replies `{"type": "pong"}`.

---

## Server → client frames

### `session_ready`

```json
{
  "type": "session_ready",
  "session_id": "uuid",
  "protocol_version": 1,
  "resume": false,
  "capabilities": {
    "agentic_events": false,
    "tool_confirmation": false,
    "speculative_prefetch": false,
    "prefix_cache": false
  }
}
```

`resume=true` means the server reloaded prior turn state for the given
`previous_response_id` (Phase 3+).

### `response.metadata`

Sent once per turn before any tokens. Same shape as the SSE
`metadata` event in `/v1/chat/stream`.

### `response.token`

Streams sanitized text chunks. Multiple per turn.

```json
{"type": "response.token", "delta": "Hello "}
```

#### Short-circuit turns (single token frame)

Guarded or templated turns skip the LLM stream and arrive as one bundled
payload: `response.metadata` extended with `faithfulness_score`,
`escalation_required`, `workflow`, and `handoff`, then a **single**
`response.token` carrying the full reply, then `response.done`. This covers
`retrieval_mode` ∈ {`blocked`, `abstained`, `clarification`, `workflow`,
`escalated`, `calculator`} and deterministic procedural replies (curated
TIN-registration / return-filing answers), which carry their real
faithfulness score — 1.0 for curated templates — instead of an LLM-stream
estimate. `calculator` frames are instant tax computations from the
deterministic calculator router; a calculation missing its figures arrives
as a `workflow` frame instead (guided slot-filling for the absent details).

### `response.retrieval.*` *(Phase 2)*

`retrieval.started`, `retrieval.completed`, `rerank.completed`. Carries
the same `sources` / `citations` payload that today appears in `metadata`.

### `response.tool_call.*` *(Phase 2)*

```json
{"type": "response.tool_call.started",
 "call_id": "tc_01", "name": "calculate_vat",
 "arguments": {"amount_ugx": 5000000}}
```

```json
{"type": "response.tool_call.completed",
 "call_id": "tc_01", "ok": true,
 "result_summary": "VAT = 900,000 UGX", "elapsed_ms": 18}
```

### `response.tool_call.skipped`

Emitted instead of `started`/`completed` when the turn's spend budget
declines a call — see `docs/agentic-loop.md`. `admission` is `repeat`
(an identical call already ran this turn; the earlier result is reused)
or `denied` (a ceiling was hit). Nothing was dispatched either way.

```json
{"type": "response.tool_call.skipped",
 "call_id": "tc_03", "name": "lookup_rate",
 "admission": "repeat", "reason": "duplicate call", "iteration": 2}
```

### `response.tool_call.confirmation_required` *(Phase 4)*

```json
{"type": "response.tool_call.confirmation_required",
 "call_id": "tc_02", "name": "submit_vat_return",
 "proposal": {"action_type": "...", "payload": {...}},
 "confirm_token": "ct_signed",
 "idempotency_key": "ik_..."}
```

The turn pauses for up to `CONFIRMATION_TIMEOUT_S` (default 120 s)
awaiting `tool_call.confirm` from the client.

### `response.grounding`

```json
{"type": "response.grounding",
 "faithfulness_score": 0.87,
 "escalation_required": false,
 "response_judge": {...}}
```

### `response.revision`

Emitted when the response judge revises an answer; the revised text
replaces accumulated `response.token` content client-side.

### `response.done`

Final frame for a turn. After this the socket is ready for the next
`response.create`.

### `response.error`

```json
{"type": "response.error",
 "code": "not_implemented" | "deadline_exceeded" | "internal" | ...,
 "message": "human-readable"}
```

Followed by `response.done` unless the error is fatal to the session,
in which case the socket is closed with code 1011.

### `response.cancelled`

Acknowledges a `response.cancel`. Implies a terminal state for the turn.

### `session.expired`

Sent immediately before a server-initiated close at the 60-min cap.

### `error` (envelope)

Out-of-turn errors not tied to a particular response use the envelope
shape from `voice_ws_v2`:

```json
{"type": "error", "detail": "...", "recoverable": true|false}
```

---

## Close codes

| Code | Meaning |
|------|---------|
| 1000 | Normal closure (`session_end`, session expired). |
| 1001 | Feature flag disabled. |
| 1008 | Authentication failed. |
| 1011 | Unrecoverable server error. |

---

## Feature flag rollout

| Phase | Flag                | What lights up |
|-------|---------------------|----------------|
| 0     | `ws_chat`           | Lifecycle, stub `response.create`. |
| 1     | `ws_chat`           | Real chat pipeline + cancellation. |
| 2     | `ws_chat`           | Tool/retrieval events, lifted iteration cap. |
| 3     | `prefix_caching`    | KV prefix cache hint (vLLM only). |
| 4     | `audit_ledger`      | Tool confirmation audit chain. |
| 5     | —                   | Unified `/v2/agent/stream`. |
| 6     | `trace_to_client`   | OTel span mirroring. |

---

## Compatibility guarantees

* SSE `/v1/chat/stream` event payloads are a subset of the WS event
  payloads — a client that understands SSE can be ported by changing
  only the transport and the routing of new event types.
* Anonymous sessions are allowed (mirrors `optional_user` on the HTTP
  endpoints). `auth_required=true` enforces a JWT.
* Production validation (Phase 6) refuses to start the server when
  `FLAG_WS_CHAT=true` and `FLAG_AUTH_REQUIRED=false` in `APP_ENV=production`.


---

## `WS /v1/admin/tickets/stream` — staff escalation events

A separate socket from the chat protocol above. Staff roles only
(`ura_staff`, `ura_admin`, `ura_auditor`); anything else is closed with
`4403` before the handshake completes. Missing or invalid auth closes
with `4401`.

Read-only: the client sends nothing.

```json
{"type": "subscribed", "team": "customs"}
```

```json
{"type": "escalation.created", "id": "tkt-1", "priority": "urgent",
 "status": "open", "team": "customs", "reason": "...",
 "topic": "objection_or_dispute", "sentiment": "frustration",
 "transfer_style": "warm", "created_at": 1754400000.0}
```

```json
{"type": "ping"}
```

`?team=customs` filters to one queue. The payload never contains the
transcript, `user_query`, `bot_reply` or `staff_note` — fetch the ticket
through `GET /v1/admin/tickets/{id}` for those.
