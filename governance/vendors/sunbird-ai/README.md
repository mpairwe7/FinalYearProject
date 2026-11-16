# Sunbird AI

Read 2026-11-17 from [docs.sunbird.ai](https://docs.sunbird.ai/support/faq.md).

| Slot | Record |
| --- | --- |
| Role | The primary answer generator on the CPU deployments (Sunflower, `sunflower-9b`), the last-resort FAQ judge and the translation fallback (ADR-0036, ADR-0037). Speech recognition and text-to-speech on the Space. |
| Agreement | None published. The FAQ says: "We offer a free tier for developers and researchers. For high-volume commercial use, please contact us." No terms of service, privacy policy or acceptable use policy is linked from Sunbird's pages. |
| Data that leaves | The taxpayer's question, recent conversation turns and the retrieved passages; caller audio for speech recognition; text for speech and translation. |
| Training and retention | Not published. |
| Breach notification | Not published. |
| Status | Both accounts answer 403 `ACCOUNT_SUSPENDED` (G539, [incident log](../../evidence/incident_log/README.md)). |
| Price | Not published ([cost_per_decision.csv](../../monitoring/cost_per_decision.csv)). |

Open for the owner: obtain written terms that cover retention, training use,
breach notification and cross-border transfer before personal data goes to
Sunbird in production (G665).
