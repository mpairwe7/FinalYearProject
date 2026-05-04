#!/usr/bin/env bash
set -euo pipefail

# Pick the least-busy GPU by preferring low memory usage, then low SM load,
# with free memory as a final tie-breaker.
if ! command -v nvidia-smi >/dev/null 2>&1; then
    exit 1
fi

nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu \
    --format=csv,noheader,nounits \
    | awk -F',' '{
        gsub(/ /, "", $1)
        gsub(/ /, "", $2)
        gsub(/ /, "", $3)
        gsub(/ /, "", $4)
        free_mem = $3 - $2
        printf "%s,%d,%d,%d\n", $1, $2, free_mem, $4
    }' \
    | sort -t',' -k2,2n -k4,4n -k3,3nr \
    | head -n1 \
    | cut -d',' -f1
