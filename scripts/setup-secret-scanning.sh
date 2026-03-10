#!/usr/bin/env bash
# =============================================================================
# Setup Script: Secret Scanning & Pre-commit Hooks (2026 Industry Standard)
#
# This script installs and configures a 4-layer defence-in-depth secret
# scanning pipeline for the URA Chatbot project:
#
#   1. TruffleHog v3  – verified credential detection (800+ detectors)
#   2. ggshield       – GitGuardian ML-based detection (400+ types)
#   3. Gitleaks v8    – regex + entropy (150+ rules + custom URA patterns)
#   4. detect-secrets – Yelp baseline-aware entropy scanner
#
# Usage:
#   bash scripts/setup-secret-scanning.sh
#
# Prerequisites:
#   - Python 3.11+
#   - Git 2.40+
#   - Internet connection (for installing tools)
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()  { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }

# ── Verify we're in the project root ───────────────────────────────────────
if [ ! -f "pyproject.toml" ] || [ ! -f ".pre-commit-config.yaml" ]; then
    error "Run this script from the project root: bash scripts/setup-secret-scanning.sh"
fi

# ── Check prerequisites ───────────────────────────────────────────────────
step "Checking prerequisites"
command -v python3 >/dev/null 2>&1 || error "python3 is required (3.11+)"
command -v git     >/dev/null 2>&1 || error "git is required (2.40+)"

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "Python $PYTHON_VERSION detected"
info "Git $(git --version | cut -d' ' -f3) detected"

# ── Install Python tools ──────────────────────────────────────────────────
step "Installing Python tools"
pip install --quiet --upgrade pre-commit detect-secrets ggshield 2>/dev/null \
    || pip install --quiet --upgrade --user pre-commit detect-secrets ggshield
info "Installed: pre-commit, detect-secrets, ggshield"

# ── Install pre-commit hooks ──────────────────────────────────────────────
step "Installing Git hooks"
pre-commit install --hook-type pre-commit
pre-commit install --hook-type pre-push
info "Installed pre-commit and pre-push hooks into .git/hooks/"

# ── Generate detect-secrets baseline ──────────────────────────────────────
step "Configuring detect-secrets baseline"
if [ ! -f .secrets.baseline ]; then
    info "Generating new detect-secrets baseline..."
    detect-secrets scan \
        --exclude-files '\.secrets\.baseline$' \
        --exclude-files 'package-lock\.json$' \
        --exclude-files 'bun\.lockb$' \
        --exclude-files 'bun\.lock$' \
        --exclude-files 'pubspec\.lock$' \
        --exclude-files 'Podfile\.lock$' \
        --exclude-files '\.env\.example$' \
        --exclude-files '.*\.ipynb$' \
        --exclude-files 'Data/.*' \
        --exclude-files 'Results/.*' \
        --exclude-files 'test_env/.*' \
        --exclude-files 'artifacts/.*' \
        --exclude-files 'sbom-cyclonedx\.json$' \
        --exclude-files '.*\.apk$' \
        --exclude-files '.*\.ipa$' \
        > .secrets.baseline
    info "Baseline created at .secrets.baseline"
    info "Review and commit this file: git add .secrets.baseline && git commit"
else
    info "Baseline exists — updating with current scan..."
    detect-secrets scan --baseline .secrets.baseline \
        --exclude-files '\.secrets\.baseline$' \
        --exclude-files 'package-lock\.json$' \
        --exclude-files 'bun\.lockb$' \
        --exclude-files '.*\.ipynb$' \
        --exclude-files 'Data/.*' \
        --exclude-files 'Results/.*'
    info "Baseline updated"
fi

# ── Install TruffleHog (if not present) ───────────────────────────────────
step "Installing TruffleHog v3"
if command -v trufflehog >/dev/null 2>&1; then
    info "TruffleHog already installed: $(trufflehog --version 2>/dev/null || echo 'version unknown')"
else
    if command -v brew >/dev/null 2>&1; then
        brew install trufflehog
    else
        TRUFFLEHOG_VERSION="3.88.0"
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64)  ARCH="amd64" ;;
            aarch64) ARCH="arm64" ;;
        esac
        OS=$(uname -s | tr '[:upper:]' '[:lower:]')
        INSTALL_DIR="${HOME}/.local/bin"
        mkdir -p "$INSTALL_DIR"
        curl -sSfL "https://github.com/trufflesecurity/trufflehog/releases/download/v${TRUFFLEHOG_VERSION}/trufflehog_${TRUFFLEHOG_VERSION}_${OS}_${ARCH}.tar.gz" \
            | tar xz -C "$INSTALL_DIR" trufflehog 2>/dev/null \
            && info "TruffleHog installed to $INSTALL_DIR/trufflehog" \
            || warn "Could not install TruffleHog. Install manually: https://github.com/trufflesecurity/trufflehog#installation"

        # Ensure ~/.local/bin is in PATH
        if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
            warn "Add $INSTALL_DIR to your PATH: export PATH=\"$INSTALL_DIR:\$PATH\""
        fi
    fi
fi

# ── Install Gitleaks (if not present) ─────────────────────────────────────
step "Installing Gitleaks v8"
if command -v gitleaks >/dev/null 2>&1; then
    info "Gitleaks already installed: $(gitleaks version 2>/dev/null || echo 'version unknown')"
else
    if command -v brew >/dev/null 2>&1; then
        brew install gitleaks
    else
        GITLEAKS_VERSION="8.24.0"
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64)  ARCH="x64" ;;
            aarch64) ARCH="arm64" ;;
        esac
        OS=$(uname -s | tr '[:upper:]' '[:lower:]')
        INSTALL_DIR="${HOME}/.local/bin"
        mkdir -p "$INSTALL_DIR"
        curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${OS}_${ARCH}.tar.gz" \
            | tar xz -C "$INSTALL_DIR" gitleaks 2>/dev/null \
            && info "Gitleaks installed to $INSTALL_DIR/gitleaks" \
            || warn "Could not install Gitleaks. Install manually: https://github.com/gitleaks/gitleaks#installation"
    fi
fi

# ── Validate configuration ────────────────────────────────────────────────
step "Validating configuration"

# Check all config files exist
CONFIGS=(".pre-commit-config.yaml" ".gitleaks.toml" ".gitguardian.yaml" ".trufflehog-exclude-paths.txt")
ALL_PRESENT=true
for cfg in "${CONFIGS[@]}"; do
    if [ -f "$cfg" ]; then
        info "  $cfg"
    else
        warn "  $cfg — MISSING"
        ALL_PRESENT=false
    fi
done

# Validate pre-commit config syntax
info "Validating pre-commit config..."
pre-commit validate-config .pre-commit-config.yaml

# Check ggshield API key
if [ -z "${GITGUARDIAN_API_KEY:-}" ]; then
    warn "GITGUARDIAN_API_KEY not set — ggshield hook will fail until configured"
    warn "  Get a free key: https://dashboard.gitguardian.com/api/personal-access-tokens"
    warn "  Then: export GITGUARDIAN_API_KEY=your_key"
fi

# ── Run initial scan ──────────────────────────────────────────────────────
step "Running initial hook test"
info "This may take 1-2 minutes on first run (downloading hook environments)..."
pre-commit run --all-files 2>&1 || warn "Some hooks reported findings — review output above"

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}  Secret Scanning Setup Complete!${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
echo "  Pre-commit hooks installed (4 layers):"
echo "    1. TruffleHog v3    – verified credential detection"
echo "    2. ggshield         – GitGuardian ML-based detection"
echo "    3. Gitleaks v8      – regex + entropy (+ custom URA rules)"
echo "    4. detect-secrets   – baseline-aware entropy scanner"
echo ""
echo "  Additional hygiene hooks:"
echo "    - detect-private-key     – blocks .pem/.key files"
echo "    - check-added-large-files – blocks files >500 KB"
echo "    - no-commit-to-branch    – blocks direct commits to main"
echo "    - check-case-conflict    – prevents case-insensitive collisions"
echo ""
echo "  CI workflow: .github/workflows/secret-scanning.yml"
echo "    Runs on: push, PR, weekly full-history scan"
echo ""
echo "  Configuration files:"
echo "    - .pre-commit-config.yaml       – hook definitions"
echo "    - .gitleaks.toml                – custom rules (Uganda PII, ML keys)"
echo "    - .gitguardian.yaml             – ggshield path exclusions"
echo "    - .trufflehog-exclude-paths.txt – TruffleHog path exclusions"
echo "    - .secrets.baseline             – detect-secrets known false-positives"
echo ""
echo "  Next steps:"
echo "    1. Set GITGUARDIAN_API_KEY locally and in GitHub Secrets"
echo "    2. Review .secrets.baseline: detect-secrets audit .secrets.baseline"
echo "    3. Commit all config files"
echo "    4. Test: pre-commit run --all-files"
echo ""
