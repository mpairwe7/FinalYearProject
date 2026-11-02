# docs/

Architecture, ADRs (`docs/adr/`), operational runbooks (`docs/runbooks/`), model card (`docs/MODEL_CARD.md`), and the gap register (`docs/GAPS_AND_AGENTIC_ROADMAP.md`).

After any behaviour, model, route, flag, or architecture change, run both:
- `.claude/skills/refresh-docs/SKILL.md` (`refresh-docs`)
- `.claude/skills/living-docs-adr-runbooks-traceability/SKILL.md` (`living-docs-adr-runbooks-traceability`)

All documentation updates must follow 2026 industry documentation & AI governance standards (Diátaxis framework, MADR 4.0 ADRs, ISO/IEC 42001:2023 Clause 7.5, NIST AI RMF 1.0 `GOVERN 1.4` & `MAP 4.2`, EU AI Act Art. 11/12/50/53, Mitchell et al. Model Cards, and CycloneDX 1.6 AIBOMs), and every open PR must resolve all CI failures and CodeRabbit AI review comments before merge. A new flag must land in `flags.py`, `docs/RAG_ARCHITECTURE.md`, and `.env.example`. Every new architectural decision must land in `docs/adr/` and be indexed in `docs/adr/README.md`.
