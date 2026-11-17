# Google Gemini API

Read 2026-11-17 from Google's published pages. Quotes are verbatim.

| Slot | Record |
| --- | --- |
| Role | No longer an answer generator (ADR-0036), FAQ judge or translator (ADR-0037). It backs the opt-in speech recognition and text-to-speech fallbacks and the call receptionist's Gemini Live, which is off on the HF Space. |
| Agreement | The [Gemini API Additional Terms](https://aistudio.google.com/docs/terms), effective 2026-03-23, and on the paid tier Google's [processor terms](https://business.safety.google/processorterms/). No negotiated contract is recorded. |
| Data that leaves | Caller audio and text for the fallbacks that are switched on. Calls can route through Cloudflare AI Gateway ([Cloudflare](../cloudflare-workers-ai/README.md)). |
| Training and retention | Paid tier: "Google doesn't use your prompts (...) or responses to improve our products", and "Google logs prompts and responses for a limited period of time, solely for detecting and preventing violations". Unpaid tier: Google uses the content "to provide, improve, and develop", and "human reviewers may read, annotate, and process your API input and output". |
| Breach notification | Processor terms section 7.2.1: Google will "notify Partner of the Data Incident promptly and without undue delay". |
| Model changes | From 2026-12-01, Google routes every `gemini-2.5-flash` request to Gemini 3.5 Flash-Lite ([pricing](https://aistudio.google.com/docs/pricing)). The configured `GEMINI_MODEL`, `gemini-3.7-flash`, is not on the pricing page. |
| Price | [cost_per_decision.csv](../../monitoring/cost_per_decision.csv) |

Open for the owner: confirm the key is on the paid tier before any personal data
reaches it. The unpaid tier allows training and human review. Confirm that
`gemini-3.7-flash` is a model Google still serves.
