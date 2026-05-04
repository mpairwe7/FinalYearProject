#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# reindex.sh — Drop-and-rebuild the Qdrant vector index
#
# Usage:
#   ./scripts/reindex.sh                   # full re-index (PDFs + CSVs)
#   ./scripts/reindex.sh --csvs-only       # FAQ CSVs only
#   ./scripts/reindex.sh --pdfs-only       # PDFs only
#
# When to run:
#   - After changing DENSE_MODEL or DENSE_DIM (embedding dimension change)
#   - After adding/removing documents in Data/dataset or Data/pdfs
#   - After a fresh deployment with an empty Qdrant volume
#
# Prerequisites:
#   - Qdrant must be running  (docker compose up qdrant)
#   - Python venv with project deps installed
#
# See docs/MODEL_SWAP_GUIDE.md for full model-swap instructions.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colours (disabled when piped)
if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; NC=''
fi

info()  { echo -e "${GREEN}[reindex]${NC} $*"; }
warn()  { echo -e "${YELLOW}[reindex]${NC} $*"; }
error() { echo -e "${RED}[reindex]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"

info "Checking Qdrant at ${QDRANT_URL} ..."
if ! curl -sf "${QDRANT_URL}/healthz" >/dev/null 2>&1; then
  error "Qdrant is not reachable at ${QDRANT_URL}"
  error "Start it with:  docker compose up -d qdrant"
  exit 1
fi
info "Qdrant is healthy"

# Show current config
DENSE_MODEL="${DENSE_MODEL:-BAAI/bge-m3}"
DENSE_DIM="${DENSE_DIM:-1024}"
COLLECTION="${QDRANT_COLLECTION:-ura_knowledge_base}"

info "Config:"
info "  DENSE_MODEL  = ${DENSE_MODEL}"
info "  DENSE_DIM    = ${DENSE_DIM}"
info "  COLLECTION   = ${COLLECTION}"

# ---------------------------------------------------------------------------
# Run the indexer
# ---------------------------------------------------------------------------
info "Starting re-index (--recreate) ..."
cd "$PROJECT_ROOT"
python -m App.backend.app.indexer --recreate "$@"

# ---------------------------------------------------------------------------
# Post-index verification
# ---------------------------------------------------------------------------
info "Verifying collection ..."
COLLECTION_INFO=$(curl -sf "${QDRANT_URL}/collections/${COLLECTION}" 2>/dev/null || true)

if [ -z "$COLLECTION_INFO" ]; then
  error "Could not fetch collection info — verify manually"
  exit 1
fi

# Extract vectors_count using python (available in project venv)
VECTORS=$(echo "$COLLECTION_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('result', {}).get('vectors_count', 0))
" 2>/dev/null || echo "0")

VECTOR_SIZE=$(echo "$COLLECTION_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
dense = data.get('result', {}).get('config', {}).get('params', {}).get('vectors', {}).get('dense', {})
print(dense.get('size', 'unknown'))
" 2>/dev/null || echo "unknown")

if [ "$VECTORS" = "0" ]; then
  error "Collection has 0 vectors — indexing may have failed"
  exit 1
fi

info "Re-index complete!"
info "  vectors_count = ${VECTORS}"
info "  vector_size   = ${VECTOR_SIZE}"
info ""
info "If you changed the embedding model, restart the API to pick it up:"
info "  docker compose restart api"
