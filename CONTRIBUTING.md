# Contributing to URA Chat Bot MLOps Pipeline

Thank you for your interest in contributing to the URA Chat Bot project. This document provides guidelines for contributing.

## Code of Conduct

All contributors must adhere to the [ACM Code of Ethics](https://www.acm.org/code-of-ethics) and the [IEEE Code of Ethics](https://www.ieee.org/about/corporate/governance/p7-8.html). We are committed to providing a welcoming, inclusive, and harassment-free environment.

## How to Contribute

### Reporting Issues

1. Check existing issues to avoid duplicates.
2. Use the appropriate issue template (bug report, feature request, security vulnerability).
3. For **security vulnerabilities**, follow the process in [SECURITY.md](SECURITY.md) instead.

### Development Workflow

1. **Fork** the repository.
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make changes** following our coding standards (below).
4. **Test** your changes locally.
5. **Commit** with conventional commit messages:
   ```
   feat: add VAT rate lookup endpoint
   fix: correct PII redaction regex for Uganda NINs
   docs: update API reference for /v1/chat
   ```
6. **Push** and open a **Pull Request** against `main`.

### Coding Standards

#### Python (Backend / ML)
- Package manager: **uv** (replaces pip)
- Formatter: **Black** (line length 88)
- Linter: **Ruff**
- Type checker: **MyPy** (strict mode)
- Docstrings: Google style
- Test framework: **pytest**
- LLM: **Qwen2.5-3B-Instruct** via HuggingFace transformers (local inference)

#### TypeScript (Frontend)
- Formatter: **Prettier**
- Linter: **ESLint** with `next/core-web-vitals`
- Package manager: **Bun**

#### General
- No secrets or credentials in code (use `.env` files, reference `.env.example`)
- All new endpoints must include Pydantic request/response models
- All new features must include tests
- All changes must pass CI checks before merge

### Data Contributions

If contributing training data:
- Ensure data is **publicly available** or you have explicit rights to contribute it.
- Include **provenance metadata** (source, date collected, license).
- Run the data quality gate: `python -m ml.pipelines.validate_data`
- No PII (personally identifiable information) in training data.

### AI/ML Model Changes

- Document any changes to model architecture, hyperparameters, or training data in the PR description.
- Include evaluation metrics comparison (before/after).
- Ensure the quality gate thresholds in `ml/pipelines/quality_gates.py` still pass.

## Review Process

1. All PRs require at least one approving review from a CODEOWNER.
2. CI must pass (linting, type checking, tests, security scans).
3. Documentation must be updated if the PR changes public APIs or user-facing behaviour.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
