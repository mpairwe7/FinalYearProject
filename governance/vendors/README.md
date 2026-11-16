# vendors/

One folder per AI provider. The providers are the `provider` entries in
`aibom_inventory` in the [manifest](../systems/ura-ai-chatbot/manifest.yaml).
Each folder records the agreement, the data that leaves, training and retention
terms and the breach notification duty, quoted from the provider's published
pages.

| Vendor | Agreement | Breach notification |
| --- | --- | --- |
| [Cloudflare Workers AI](cloudflare-workers-ai/README.md) | Online terms and Customer DPA v6.4 | "without undue delay" (DPA 3.1(e)) |
| [Google Gemini API](google-gemini-api/README.md) | Gemini API Additional Terms and processor terms | "promptly and without undue delay" (7.2.1) |
| [Sunbird AI](sunbird-ai/README.md) | None published | None published (G665) |

| Slot | State | Artifact |
| --- | --- | --- |
| Contract | Here, partly | Each vendor folder names the online terms that govern use. No negotiated contract or AI-specific clause exists with any provider, and Sunbird publishes no terms at all (G665). |
| Data flows | Here | Each vendor folder says what data leaves. The cross-border row in [privacy_controls.md](../systems/ura-ai-chatbot/privacy_controls.md) and the [DPIA](../systems/ura-ai-chatbot/dpia.md) cover transfer approval, and [threagile.yaml](../../docs/security/threat-model/threagile.yaml) models the flows. |
| Breach notification | Here, partly | Cloudflare and Google commit to notify without undue delay, with no fixed hours. Sunbird publishes nothing. The project's own notice to taxpayers is section 1.7 of the [privacy notice](../../docs/capstone/week04-privacy-and-aup.md). |
