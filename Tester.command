#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "Lancez d'abord Installer.command."
  exit 1
fi
exec .venv/bin/python -m pytest tests -v "$@"
