# Cloudflare Workers AI

Read 2026-11-17 from Cloudflare's published pages. Quotes are verbatim.

| Slot | Record |
| --- | --- |
| Role | The second, third and fourth answer tiers behind Sunbird: `@cf/meta/llama-3.3-70b-instruct-fp8-fast`, then `@cf/mistralai/mistral-small-3.1-24b-instruct`, then `@cf/meta/llama-4-scout-17b-16e-instruct` (ADR-0036). Reached through the Crane Cloud relay, or directly when the relay fails. |
| Agreement | Cloudflare's online terms and the [Customer DPA](https://cloudflare.com/cloudflare-customer-dpa/), version 6.4, effective 2026-04-03. No negotiated contract or AI-specific clause is recorded. |
| Data that leaves | The taxpayer's question, recent conversation turns and the retrieved passages, in the prompt. |
| Training and retention | "Cloudflare does not use Customer Content processed by Workers AI to train AI models." "Workers AI processes Customer Content only for as long as needed to provide the service." Content is stored when a request passes through AI Gateway with logging or caching on. `providers/gateway.py` can route Workers AI and Gemini calls through AI Gateway, so its log setting decides retention ([data usage](https://developers.cloudflare.com/workers-ai/platform/data-usage/)). |
| Breach notification | DPA clause 3.1(e): Cloudflare will "without undue delay notify the Customer upon becoming aware of any breach of security". No number of hours is stated. |
| Price | [cost_per_decision.csv](../../monitoring/cost_per_decision.csv) |

Open for the owner: confirm the AI Gateway log setting, and record the transfer
approval that [privacy_controls.md](../../systems/ura-ai-chatbot/privacy_controls.md)
requires before production.
