#!/usr/bin/env bash
# Pre-hook that blocks Read/Write/Edit access to /tmp/.
# Use tmp/ (repo-local scratch directory) instead.
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [[ "$FILE_PATH" == /tmp/* ]]; then
  echo "Blocked: never use /tmp/. Use tmp/ (repo-local scratch directory) instead." >&2
  exit 2
fi

exit 0
