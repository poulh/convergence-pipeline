#!/bin/bash
# Create the project's virtual environment and install the pipeline tools.
# Safe to re-run; the orchestrator skill runs this when .venv is missing.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "creating .venv"
  python3 -m venv .venv
fi

./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e .

echo "ready. tools:"
ls .venv/bin | grep '^cp-' | sed 's/^/  /'
echo
echo "activate with:  source .venv/bin/activate"
