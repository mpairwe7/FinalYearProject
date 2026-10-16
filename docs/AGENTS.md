# docs/

Architecture, ADRs (`docs/adr/`), operational runbooks (`docs/runbooks/`), model card (`docs/MODEL_CARD.md`), and the gap register (`docs/GAPS_AND_AGENTIC_ROADMAP.md`).

After any behaviour, model, route, flag, or architecture change, run both:
- `.claude/skills/refresh-docs/SKILL.md` (`refresh-docs`)
- `.claude/skills/living-docs-adr-runbooks-traceability/SKILL.md` (`living-docs-adr-runbooks-traceability`)

A new flag must land in `flags.py`, `docs/RAG_ARCHITECTURE.md`, and `.env.example`. Every new architectural decision must land in `docs/adr/` and be indexed in `docs/adr/README.md`.
