# FinalYearProject

This repository describes a CI/CD pipeline for developing and training a customer-service conversation AI. The pipeline uses GitHub Actions for automation, Kaggle for model training, and Vercel for hosting the frontend UI.

## Pipeline Overview
- **Code**: Source, data configs, and frontend live in this repo on GitHub.
- **CI (GitHub Actions)**: Lint/test backend and UI, validate dataset configs, and build artifacts.
- **Model Training (Kaggle)**: GitHub Actions launches Kaggle notebook jobs via API for training; artifacts (checkpoints/metrics) are pushed back to GitHub Releases or an object store.
- **CD (Vercel)**: Successful main-branch builds trigger Vercel deployments for the frontend.

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

## Required Secrets
- `KAGGLE_USERNAME`, `KAGGLE_API_TOKEN`: Kaggle API access for training jobs (API token JSON string).
- `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`: Vercel deployment auth.
- Any additional tokens for artifact storage (e.g., `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` or GitHub PAT).

## Local Development
- Install Python/Node deps and run lint/tests locally to match CI.
- Keep Kaggle notebook entrypoint versioned; ensure data paths/configs are reproducible.
- Frontend uses the same build command locally as in Actions.

## Next Steps
- Workflows are defined under `.github/workflows/` (CI, Kaggle train, Vercel deploy).
- Set repository secrets in GitHub settings before running workflows.
- Expand data and evaluation documentation as the schema matures.