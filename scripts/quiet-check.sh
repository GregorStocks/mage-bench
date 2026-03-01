#!/usr/bin/env bash
set -euo pipefail

# Runs each check target. Quiet by default (one line per step).
# Pass -v for verbose output.

verbose=false
if [[ "${1-}" == "-v" ]]; then
    verbose=true
fi

targets=(lint format-check typecheck test test-js verify-decks verify-schema-types verify-mcp-tools)

for target in "${targets[@]}"; do
    if $verbose; then
        make "$target"
    else
        printf "  %-25s" "$target"
        if output=$(make "$target" 2>&1); then
            echo "ok"
        else
            echo "FAIL"
            echo ""
            echo "$output"
            exit 1
        fi
    fi
done
