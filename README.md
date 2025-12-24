# FinalYearProject

This repository describes a CI/CD pipeline for developing and training a customer-service conversation AI. The pipeline uses GitHub Actions for automation, Kaggle for model training, and Vercel for hosting the frontend UI. Backend uses Python/FastAPI with `uv` for dependency management; frontend (Next.js) uses Bun.

## Pipeline Overview
- **Code**: Source, data configs, and frontend live in this repo on GitHub.
- **CI (GitHub Actions)**: Lint/test backend and UI, validate dataset configs, and build artifacts.
- **Model Training (Kaggle)**: GitHub Actions launches Kaggle notebook jobs via API for training; artifacts (checkpoints/metrics) are pushed back to GitHub Releases or an object store.
- **CD (Vercel)**: Successful main-branch builds trigger Vercel deployments for the frontend.
- **API Container**: FastAPI backend packaged via Docker; images pushed to DockerHub for deployment.

## URA Chatbot specifics
- PDF ingestion -> chunking -> embeddings -> database -> retrieval-augmented chatbot UI.
- Data model, ingestion flow, and evaluation rubric are documented in [docs/data-schema-and-eval.md](docs/data-schema-and-eval.md).

## GitHub Actions Workflows
Create workflows under `.github/workflows/`:

1) **ci.yml** (runs on PRs and pushes):
	- Checkout repo and set up Python/Node.
	- Install deps (cache pip/npm) and run lint + unit tests.
	- Validate dataset schema/config (e.g., JSON/YAML check).
	- Build frontend bundle (for preview artifacts) and optionally upload to the Actions run.

2) **train.yml** (manual or on schedule):
	- Checkout repo.
	- Retrieve Kaggle API token from `KAGGLE_USERNAME`/`KAGGLE_API_TOKEN` secrets (token JSON written to kaggle.json).
	- Trigger Kaggle notebook/competition job using `kaggle kernels push` or the REST API.
	- Poll for completion; on success, download model artifacts/metrics.
	- Upload trained model to GitHub Releases or an object store; publish metrics as job summary.

3) **deploy-frontend.yml** (runs on main after CI success):
	- Checkout repo and build frontend.
	- Use `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` secrets to run `vercel deploy --prod`.
	- Post deployment URL to the commit/PR.

4) **deploy-api.yml** (push to DockerHub):
	- Checkout repo.
	- Login to DockerHub with `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`.
	- Build and push FastAPI image (`landwind/ura-chatbot-api`) tagged with `latest` and commit SHA.

## Required Secrets
- `KAGGLE_USERNAME`, `KAGGLE_API_TOKEN`: Kaggle API access for training jobs (API token JSON string).
- `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`: Vercel deployment auth.
- `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`: DockerHub push auth (set username to `landwind`, token from DockerHub; do not commit passwords).
- Any additional tokens for artifact storage (e.g., `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` or GitHub PAT).

## Local Development
- Install Python deps with `uv pip install -r requirements.txt` (or `uv sync` if using a lockfile) and run lint/tests via `uv run ruff/pytest/mypy` to mirror CI.
- Frontend: install with `bun install`; run `bun run lint/test/build` matching CI.
- Keep Kaggle notebook entrypoint versioned; ensure data paths/configs are reproducible.
- API: build and run locally with `docker compose up --build` (expects `app.main:app`).

## Next Steps
- Workflows are defined under `.github/workflows/` (CI, Kaggle train, Vercel deploy, DockerHub API deploy).
- Set repository secrets in GitHub settings before running workflows.
- Expand data and evaluation documentation as the schema matures.