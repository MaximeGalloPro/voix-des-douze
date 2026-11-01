#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Cette application nécessite macOS 14 ou ultérieur."
  exit 1
fi
if ! /usr/bin/python3 -I --version >/dev/null 2>&1; then
  echo "Le helper de capture nécessite Python système (Command Line Tools Apple). Installez-les avec xcode-select --install si nécessaire."
  exit 1
fi
if [[ ! -d .venv ]]; then
  PYTHON="${PYTHON:-python3.13}"
  if ! command -v "$PYTHON" >/dev/null; then
    echo "Python 3.13 est requis. Installez-le depuis python.org, puis relancez. Aucune installation globale n'est effectuée par ce script."
    exit 1
  fi
  "$PYTHON" -m venv .venv
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Le dossier .venv existe mais n'est pas un environnement valide. Aucun fichier n'a été supprimé."
  exit 1
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 13), "Le venv existant doit utiliser Python 3.13 ; il ne sera pas remplacé."'
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/prepare_models.py
printf '\nInstallation terminée. Ouvrez Lancer.command. Les lancements suivants restent hors ligne.\n'
