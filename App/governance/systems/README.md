# systems/

One folder per AI system in production. The project runs one system, the URA AI
Chatbot, in [ura-ai-chatbot/](ura-ai-chatbot/). Its chat and call receptionist
share the manifest and the model card.

| Slot | State | Artifact |
| --- | --- | --- |
| Manifest | Here | [manifest.yaml](ura-ai-chatbot/manifest.yaml) holds purpose, users, languages, the risk classification (limited risk under EU AI Act Art. 6), control status per framework and the AI bill of materials. `App/governance/compliance_check.py` reads it at this path. |
| Model card | Here | [model_card.md](ura-ai-chatbot/model_card.md) covers models and versions, training data sources, evaluation results and limitations. |
| DPIA | Here | [dpia.md](ura-ai-chatbot/dpia.md) is a decision record that waits for DPO/legal completion and signature. |
| Privacy controls | Here | [privacy_controls.md](ura-ai-chatbot/privacy_controls.md) is the engineering evidence for the Uganda Data Protection and Privacy Act controls raised in issue #306. |
| FRIA | Here | [fria.md](ura-ai-chatbot/fria.md) is a draft that waits for DPO/legal review and signature. It is voluntary while the system is classified limited risk, and it records an open question: voice emotion inference may be high risk under Annex III point 1(c) (G664). |
| Change log | Here | [change_log.md](ura-ai-chatbot/change_log.md) lists every model swap and prompt change with its decision record. [MODEL_SWAP_GUIDE.md](../../docs/MODEL_SWAP_GUIDE.md) is the swap procedure. |
| System prompts | Here | [system_prompts.md](ura-ai-chatbot/system_prompts.md) lists every system prompt, where its text lives in `App/backend/app/prompts/`, which call sends it and which test pins it. |
| Memory | Here | [memory.md](ura-ai-chatbot/memory.md) lists what the assistant remembers, where, for how long, and the consent and erasure controls. |
| Agents and workflows | Here | [agents.md](ura-ai-chatbot/agents.md) lists each agent role and workflow, its flag, and where a person must approve. |
