# URA Chatbot Development Team & Contributor Guide

## Project Overview

The URA Chatbot is a conversational AI system developed as a final year project by four computer science students at Makerere University. This document outlines the contributions of each team member and how their work is reflected in the project blog.

## Team Members and Blog Posts

### 1. Mpairwe Lauben
**Roles**: System Architecture · ML Processing · Fine-tuning
**Key Areas**:
- End-to-end, modular system architecture
- Retrieval-augmented generation (RAG) engine
- Model processing and inference (Qwen3-8B)
- LoRA / PEFT fine-tuning, including Luganda adapters

**Blog Post**:
- [Deep Dive: Mpairwe Lauben - System Architecture, ML Processing & Fine-tuning](/blog/contributor-mpairwe-lauben)

---

### 2. Olwol Philly
**Roles**: Backend Development · TTS Pipeline
**Key Areas**:
- FastAPI backend (API, service, and data layers)
- RAG orchestration and streaming responses
- Text-to-speech (TTS) pipeline for English and Luganda
- Reliability under load and API documentation

**Blog Post**:
- [Deep Dive: Olwol Philly - Backend Development & the TTS Pipeline](/blog/contributor-olwol-philly)

---

### 3. Rwemera David
**Roles**: Frontend Design · Ingestion Pipeline
**Key Areas**:
- Responsive, accessible React + TypeScript web UI
- WCAG 2.1 AA accessibility and dark/light theming
- Ingestion pipeline: crawling ura.go.ug, PDF/CSV ETL
- Deduplication, PII redaction, chunking, and embedding

**Blog Post**:
- [Deep Dive: Rwemera David - Frontend Design & the Ingestion Pipeline](/blog/contributor-rwemera-david)

---

### 4. Okwel Edgar Mark
**Roles**: Optimization · STT Pipeline · Security Implementation
**Key Areas**:
- Performance optimization and caching strategy
- Speech-to-text (STT) pipeline (ASR / Whisper) for English and Luganda
- Security implementation (OWASP LLM Top 10, guardrails)
- Encryption, PII redaction, input validation, audit logging

**Blog Post**:
- [Deep Dive: Okwel Edgar Mark - Optimization, STT Pipeline & Security](/blog/contributor-okwel-edgar-mark)

---

## Combined Efforts

All four members contributed jointly to the foundations of the project:

- **SDD Writing** — authoring the software design document
- **Report Modelling** — structuring the final project report
- **Database Modelling** — designing the data schema and relationships
- **Data Collection** — gathering URA tax content and query data
- **Data Design** — shaping how data is organised for retrieval

## App Project Flow — Activity Ownership

| Stage | Activity | Owner |
|-------|----------|-------|
| System design | System Architecture | Mpairwe Lauben |
| ML | ML Processing | Mpairwe Lauben |
| ML | Model Fine-tuning (LoRA adapters) | Mpairwe Lauben |
| Backend | Backend Development (FastAPI / APIs) | Olwol Philly |
| Voice | TTS Pipeline | Olwol Philly |
| Frontend | Frontend Design (Web UI/UX) | Rwemera David |
| Data | Ingestion Pipeline (crawl / PDF-CSV ETL) | Rwemera David |
| Performance | Optimization | Okwel Edgar Mark |
| Voice | STT Pipeline (ASR / Whisper) | Okwel Edgar Mark |
| Security | Security Implementation (OWASP LLM, guardrails) | Okwel Edgar Mark |
| Documentation | SDD Writing | All four members |
| Documentation | Report Modelling | All four members |
| Data | Database Modelling | All four members |
| Data | Data Collection | All four members |
| Data | Data Design | All four members |

---

## Blog Structure

The project blog is organized into the following categories, with dedicated posts for each:

### Team & Contributors
- [Meet the Team: URA Chatbot Development Team](/blog/meet-the-team)
  - Photos, individual roles, and combined efforts of all 4 members
  - App project-flow activity ownership
  - Collaboration methodology, project statistics, and outcomes
- [Deep Dive: Mpairwe Lauben - System Architecture, ML Processing & Fine-tuning](/blog/contributor-mpairwe-lauben)
- [Deep Dive: Olwol Philly - Backend Development & the TTS Pipeline](/blog/contributor-olwol-philly)
- [Deep Dive: Rwemera David - Frontend Design & the Ingestion Pipeline](/blog/contributor-rwemera-david)
- [Deep Dive: Okwel Edgar Mark - Optimization, STT Pipeline & Security](/blog/contributor-okwel-edgar-mark)

### Technical Documentation
- [Project Overview: Building Conversational AI for URA](/blog/project-overview)
- [System Architecture: Building a Scalable Multilingual Platform](/blog/system-architecture)

### Features
- [Bilingual Support: English and Luganda Integration](/blog/bilingual-support)

### Security & Compliance
- [Security and Compliance: Protecting Taxpayer Data](/blog/security-compliance)

### Quality Assurance
- [Testing and Quality Assurance: Ensuring Reliability](/blog/testing-qa)

### Operations
- [Deployment and Operations: From Lab to Production](/blog/deployment-operations)

---

## How to Contribute

If you're interested in contributing to the URA Chatbot project:

### Code Contributions
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes with clear, descriptive commits
4. Submit a pull request with:
   - Description of changes
   - Rationale for the approach
   - Tests (if applicable)
   - Documentation updates

### Blog Documentation
To add or update contributor content in the blog:

1. Edit `lib/posts.ts` (team data lives in `lib/team.ts`)
2. Add a new post object with:
   - `title`: Clear, descriptive title
   - `slug`: URL-friendly identifier
   - `date`: Date in "Month YYYY" format
   - `category`: One of: Team, Introduction, Technical, Features, Security, Quality, Operations
   - `excerpt`: 1-2 sentence summary
   - `content`: Full markdown content
   - `tags`: Relevant keywords

3. Commit with message: `docs: add contributor post about [topic]`

### Code Review Standards
All contributions are reviewed based on:
- **Code Quality**: Following project standards and best practices
- **Testing**: Adequate test coverage for new features
- **Documentation**: Clear inline comments and updated docs
- **Security**: No security vulnerabilities or regressions
- **Performance**: No degradation in system performance

---

## Development Workflow

### Communication
- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: Technical discussions and questions
- **Pull Request Reviews**: Code review and feedback

### Testing
- Unit tests: 85%+ coverage
- Integration tests: All critical paths
- End-to-end tests: User workflows
- Security tests: OWASP Top 10 verification

### Deployment
- Development → Staging → Production
- Automated testing on every commit
- Manual review before production deployment
- Automated rollback on failures

---

## Learning Outcomes

Through this project, the team gained expertise in:

- **Full-Stack Development**: Web, backend, and ML systems
- **Machine Learning**: RAG pipelines, model fine-tuning, evaluation
- **DevOps**: CI/CD, containerization, infrastructure as code
- **Security**: OWASP standards, data protection, compliance
- **User Experience**: Accessibility, responsive design, user testing
- **Software Engineering**: Code review, testing, documentation

---

## Acknowledgments

We're grateful to:
- **Makerere University**: Academic support and infrastructure
- **Uganda Revenue Authority**: Project sponsorship and partnership
- **Open Source Community**: Libraries and tools that made this possible
- **Our Mentors**: Guidance and technical expertise

---

## Future Roadmap

Potential enhancements for future contributors:
- [ ] Additional language support (Swahili, Runyankole, Acholi)
- [ ] Advanced analytics dashboard
- [ ] Feedback loop for model improvement
- [ ] Integration with other government services
- [ ] Advanced security features (biometric auth)
- [ ] Performance optimization (sub-second responses)

---

## License

This project is licensed under the Apache License 2.0. See LICENSE file for details.

---

## Questions?

For questions about contributions or the project:
- Open a GitHub Issue
- Start a GitHub Discussion
- Check the [Documentation Index](./docs/README.md)

---

**Last Updated**: June 2026
**Project Status**: Production Ready
**Team Size**: 4 core contributors
