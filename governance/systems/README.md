# systems/

One folder per AI system in production. The project runs one system, the URA AI
Chatbot, in [ura-ai-chatbot/](ura-ai-chatbot/). Its chat and call receptionist
share the manifest and the model card.

| Slot | State | Artifact |
| --- | --- | --- |
| Manifest | Here | [manifest.yaml](ura-ai-chatbot/manifest.yaml) holds purpose, users, languages, the risk classification (limited risk under EU AI Act Art. 6), control status per framework and the AI bill of materials. `governance/compliance_check.py` reads it at this path. |
| Model card | Here | [model_card.md](ura-ai-chatbot/model_card.md) covers models and versions, training data sources, evaluation results and limitations. |
| DPIA | Here | [dpia.md](ura-ai-chatbot/dpia.md) is a decision record that waits for DPO/legal completion and signature. |
| Privacy controls | Here | [privacy_controls.md](ura-ai-chatbot/privacy_controls.md) is the engineering evidence for the Uganda Data Protection and Privacy Act controls raised in issue #306. |
| FRIA | Missing | EU AI Act Art. 27 asks public-body deployers of high-risk systems for a fundamental rights impact assessment. The manifest classifies this system as limited risk, and no assessment has been written. |
| Change log | Elsewhere | No single log. Each model or routing change is a decision record in [docs/adr/](../../docs/adr/README.md), and [MODEL_SWAP_GUIDE.md](../../docs/MODEL_SWAP_GUIDE.md) is the swap procedure. Prompt changes are the git history of [prompt_builder.py](../../App/backend/app/rag/prompt_builder.py). |
