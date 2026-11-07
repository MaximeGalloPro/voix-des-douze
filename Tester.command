#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "Lancez d'abord Installer.command."
  exit 1
fi
.venv/bin/python -m pytest tests --ignore=tests/test_neural_speech.py -v "$@"
if [[ -x .venv-neural/bin/python ]]; then
  .venv-neural/bin/python -m pytest tests/test_neural_speech.py -v "$@"
else
  echo "Tests réels Sopro/Pocket non exécutés : préparez les moteurs depuis l’interface."
fi
