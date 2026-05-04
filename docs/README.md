# Project Documentation Index

## 📚 Documentation Overview

Complete documentation for the URA Chatbot MLOps project.

## Quick Links

| Document | Description |
|----------|-------------|
| **Setup & Getting Started** |
| [Project Setup](PROJECT_SETUP.md) | Complete installation and setup guide |
| [Quick Start](../QUICKSTART.md) | 5-minute quick start guide |
| **CI/CD & MLOps** |
| [MLOps Workflows](mlops-workflows.md) | Comprehensive CI/CD pipeline documentation |
| [MLOps Pipeline](MLOPS_PIPELINE.md) | Pipeline architecture and implementation details |
| **Application** |
| [API Reference](API_REFERENCE.md) | REST API endpoints (sync + SSE streaming + WebSocket voice) and usage |
| [RAG Architecture](RAG_ARCHITECTURE.md) | 12-stage production RAG pipeline + streaming voice engine (2026) |
| [Gradio App](GRADIO_APP.md) | Gradio web interface documentation |
| **Data & Evaluation** |
| [Data Schema & Evaluation](data-schema-and-eval.md) | Database models, RAG pipeline, and evaluation criteria |
| **Operations & Deployment** |
| [Deployment Guide](DEPLOYMENT.md) | Production deployment, TLS, scaling, rollback, SLOs |
| [Monitoring & Observability](MONITORING.md) | OpenTelemetry, Prometheus, Grafana, alerting, SLOs |
| [Mobile Setup](MOBILE_SETUP.md) | Flutter Android/iOS build, on-device LLM, App Store compliance |
| [Mobile Architecture](MOBILE_ARCHITECTURE.md) | Layered architecture, Riverpod 2.6 Notifier, go_router, design tokens, 7 ADRs |
| **Testing & Quality** |
| [Frontend Tests](../App/frontend/vitest.config.ts) | Vitest unit/component tests + coverage thresholds |
| [E2E Tests](../App/frontend/e2e/) | Playwright smoke + axe-core WCAG 2.1 AA accessibility audit |
| [Load Tests](../tests/load/k6-chat-slo.js) | k6 SLO validation (p95 latency, error rate) |
| **Security** |
| [Security Policy](../SECURITY.md) | Vulnerability reporting, secret scanning, OWASP LLM Top 10 controls |
| [AI Red Team](../scripts/ai_red_team.py) | 50 adversarial prompts across 10 NIST AI 600-1 categories |
| [Incident Response Sim](../scripts/incident_response_sim.py) | Automated playbook validation (3 AI-specific scenarios) |
| **Governance & Compliance** |
| [AI Risk Manifest](../governance/ai_risk_manifest.yaml) | NIST AI RMF, ISO 42001, OWASP LLM, EU AI Act risk register |
| [Compliance Gate](../governance/compliance_check.py) | CI gate script (10 files + 29 keywords) |
| [Model Card](MODEL_CARD.md) | EU AI Act Article 53 model card (components, eval, ethics, limitations) |
| [Privacy Impact Assessment](capstone/PIA.md) | NDPA 2019 §28 PIA (7 risks, compliance matrix, audit trail) |
| [Bias & Fairness Audit](../scripts/bias_fairness_audit.py) | Language parity + taxpayer type parity evaluation |
| [Carbon Tracker](../scripts/carbon_tracker.py) | Training emissions tracking (CodeCarbon + Uganda grid estimate) |

## Getting Started

### For Developers
1. Read [Project Setup](PROJECT_SETUP.md) for complete installation
2. Review [MLOps Workflows](mlops-workflows.md) for CI/CD pipeline
3. Configure GitHub secrets as documented
4. Run `gh workflow run ci-ml-pipeline.yml` to trigger a build

### For ML Engineers
1. Configure Kaggle credentials ([see setup guide](PROJECT_SETUP.md#environment-configuration))
2. Update training config in `ml/configs/training_config.yaml`
3. Trigger training (TPU default): `gh workflow run kaggle-training.yml -f notebook=ura-training`
4. Optional explicit mode: `-f accelerator=tpu|gpu` and `-f run_data_eda=false`
5. Monitor results in `Results/` folder
6. Review RAG pipeline configuration in `Notebooks/ura-training.ipynb` (Sections 8–11)
   - Embedding model: change `EMBED_TARGET` in cell 3 (`fast_cpu` / `multilingual` / `multilingual_light`)
   - Index version: bump `INDEX_VERSION` when re-indexing with schema changes
   - Evaluation thresholds: adjust `MRR_THRESHOLD`, `HITATK_THRESHOLD`, `GROUNDING_THRESHOLD` in eval cells

### For Frontend Developers
1. Run `cd App/frontend && bun run dev` for local development
2. Push to `develop` for CI checks
3. Merge to `main` for production Docker build and deployment

### For API Users
1. Review [API Reference](API_REFERENCE.md) for endpoints
2. Access Swagger docs at `/docs` endpoint
3. Use provided SDK examples for integration

## Project Structure Overview

```
FinalYearProject/
├── .github/workflows/     # CI/CD pipeline definitions
├── App/                   # Application code
│   ├── app.py            # Gradio HF Spaces app
│   ├── backend/          # FastAPI backend
│   └── frontend/         # Next.js frontend
├── Data/                  # Training and reference data
│   ├── dataset/          # CSV training files
│   ├── pdfs/             # PDF documents
│   ├── TTT/              # Translation corpus
│   └── lgaudio/          # Audio files
├── governance/            # Compliance & risk management
│   ├── compliance_check.py
│   └── ai_risk_manifest.yaml
├── ml/                    # ML pipeline implementation
│   ├── configs/          # Training configuration + RAG quality gates
│   ├── pipelines/        # Training, evaluation, RAG eval, feedback export
│   ├── scripts/          # Kaggle integration
│   └── huggingface/      # HF Spaces files
├── Model/                 # Trained model artifacts
├── Results/               # Metrics and reports
│   ├── metrics/          # Training metrics
│   ├── reports/          # Validation reports
│   └── plots/            # Visualizations
├── Notebooks/             # Jupyter notebooks
├── monitoring/            # Prometheus, Grafana, alerting
│   ├── prometheus.yml    # Scrape config
│   ├── alerting-rules.yml # SLO-based alert rules (5 rules)
│   └── grafana/          # Provisioned dashboards + datasources
├── scripts/               # Operational scripts
│   ├── ai_red_team.py    # NIST AI 600-1 adversarial evaluation (50 prompts)
│   ├── bias_fairness_audit.py  # Language + taxpayer parity audit
│   ├── incident_response_sim.py # 3 AI-specific playbook simulations
│   ├── carbon_tracker.py # CodeCarbon training emissions
│   ├── validate_env.py   # Pre-deployment env validation
│   └── dr_test.sh        # Disaster recovery test (Qdrant + SQLite + health)
├── tests/
│   └── load/k6-chat-slo.js # k6 load test (p95 < 3s, error rate < 1%)
└── docs/                  # Documentation
```

## Key Workflows

### 1. Development Workflow
```
Code → Pre-commit (4 secret scanners) → Push → CI Lint + Test + Secret Scan → Review → Merge
```

### 2. ML Training Workflow
```
Data Validation → Training → Classifier Eval → RAG Eval (8 metrics) → Quality Gates → Deployment
```

### 3. Release Workflow
```
Main Branch → Governance Check → Docker Build → HF Push → Production Deploy → Feedback Loop
```

## Environment Setup

### Required Tools
- Python 3.11+ with [uv](https://docs.astral.sh/uv/) package manager
- Node.js 20+ with [Bun](https://bun.sh/) package manager
- Docker
- GitHub CLI (`gh`)

### Configuration Files
| File | Purpose |
|------|---------|
| `requirements.txt` | Python dependencies |
| `ml/configs/training_config.yaml` | Training hyperparameters |
| `docker-compose.yml` | Local development + monitoring (`--profile monitoring`) |
| `Dockerfile` | Production container image |
| `.pre-commit-config.yaml` | Pre-commit hook definitions (11 hooks: secrets, SAST, hygiene) |
| `trivy.yaml` | Trivy security scanner configuration |
| `.trivyignore` | Trivy accepted-risk suppressions |
| `.gitleaks.toml` | Gitleaks custom rules (Uganda PII, ML API keys) |
| `.gitguardian.yaml` | ggshield path exclusions |
| `.trufflehog-exclude-paths.txt` | TruffleHog path exclusions |
| `.secrets.baseline` | detect-secrets known false-positive baseline |
| `App/frontend/vitest.config.ts` | Vitest test runner + V8 coverage (60% threshold) |
| `App/frontend/playwright.config.ts` | Playwright E2E + a11y audit config |
| `App/frontend/lighthouserc.json` | Lighthouse CI (accessibility >= 90) |
| `monitoring/prometheus.yml` | Prometheus scrape targets |
| `monitoring/alerting-rules.yml` | 5 SLO alerting rules |
| `.zap-rules.tsv` | OWASP ZAP DAST rule configuration |

## Support

For issues related to:
- **CI/CD Pipeline**: Check GitHub Actions logs
- **Model Training**: Review Kaggle notebook outputs
- **Frontend**: Check Docker container logs or GitHub Actions build logs
- **Backend API**: Review Docker container logs
