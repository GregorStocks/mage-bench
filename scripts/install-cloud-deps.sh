#!/usr/bin/env bash
# Install dependencies needed to run `make update-golden` and `claim-issue.py`
# in Claude Code cloud environments.
#
# Usage: bash scripts/install-cloud-deps.sh
set -euo pipefail

echo "Installing cloud environment dependencies..."

# Ensure scratch directory exists (needed before worktree-setup.py runs)
mkdir -p tmp

# Maven (needed for make build / make update-golden)
if ! command -v mvn &>/dev/null; then
    echo "Installing Maven..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq maven
else
    echo "Maven already installed."
fi

# GitHub CLI (needed for claim-issue.py)
if ! command -v gh &>/dev/null; then
    echo "Installing GitHub CLI..."
    GH_VERSION="2.67.0"
    GH_ARCHIVE="gh_${GH_VERSION}_linux_amd64.tar.gz"
    curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${GH_ARCHIVE}" -o "tmp/${GH_ARCHIVE}"
    tar xzf "tmp/${GH_ARCHIVE}" -C tmp/
    sudo cp "tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh
    rm -rf "tmp/${GH_ARCHIVE}" "tmp/gh_${GH_VERSION}_linux_amd64"
else
    echo "GitHub CLI already installed."
fi

# Run workspace setup (creates tmp/, symlinks, .env, etc.)
uv run python scripts/worktree-setup.py

echo ""
echo "Done. You can now run:"
echo "  make build          # compile Java"
echo "  make update-golden  # update golden test fixtures"
echo "  uv run python scripts/claim-issue.py <issue>"
