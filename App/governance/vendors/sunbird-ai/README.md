# Sunbird AI

Read 2026-11-17 from Sunbird's [Terms of Service and Privacy
Policy](https://api.sunbird.ai/terms) (effective 2025-10-09, last updated
2026-08-30) and its [FAQ](https://docs.sunbird.ai/support/faq.md). The terms page
renders in the browser; its text is in the page's script bundle. Quotes are
verbatim.

| Slot | Record |
| --- | --- |
| Role | The primary answer generator on the CPU deployments (Sunflower, `sunflower-9b`), the last-resort FAQ judge and the translation fallback (ADR-0036, ADR-0037). Speech recognition and text-to-speech on the Space. |
| Agreement | Sunbird's online terms, which name its APIs among the services they govern. No negotiated contract or data processing agreement is recorded. The FAQ says: "For high-volume commercial use, please contact us." |
| Data that leaves | The taxpayer's question, recent conversation turns and the retrieved passages; caller audio for speech recognition; text for speech and translation. |
| Training and retention | "You grant Sunbird AI a perpetual, worldwide, royalty-free license to use, store, and analyze User Content to maintain, improve, and provide the Service, including for research, model training, and analytics." "When logged in, you can choose whether your User Content is used for improving our Service or training models. Without login, all data may be used for these purposes." Account content "is retained for as long as the account remains active or as needed to provide the Service". Content "incorporated into trained AI model weights" cannot be removed individually. |
| Location | "Data may be processed in Uganda or elsewhere." The terms state they follow Uganda's Data Protection and Privacy Act, 2019. |
| Breach notification | None owed to the customer. The terms say Sunbird "cannot guarantee absolute security" and ask users to "Notify us at info@sunbird.ai of any security breaches". |
| Status | Both accounts answer again since 2026-11-20; they had answered 403 `ACCOUNT_SUSPENDED` from 2026-11-09 (G539, [incident log](../../evidence/incident_log/README.md)). The primary reaches its quota and the fallback then serves. Speech recognition and text-to-speech split their 40 s deadline between the accounts, so a hung primary leaves the fallback 20 s (G714). Answer generation and Sunflower translation keep their own per-account timeouts, so there a hung primary leaves the fallback at most 10 s. |
| Price | Not published ([cost_per_decision.csv](../../monitoring/cost_per_decision.csv)). |

Open for the owner (G665): by default the terms let Sunbird train on what the
assistant sends, including personal data. Before production, confirm the API
accounts have opted out of training use, and obtain a data processing agreement
that covers retention, breach notification and transfers.
