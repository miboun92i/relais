#!/bin/zsh
cd "$(dirname "$0")" || exit 1
umask 077
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3 est nécessaire : installe-le depuis python.org.'
  read '?Appuie sur Entrée pour fermer.'
  exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
  open -e .env
  echo 'Remplis les paramètres Telegram, IA et panel dans le fichier ouvert, enregistre puis relance ce fichier.'
  read '?Appuie sur Entrée pour fermer.'
  exit 0
fi
python3 -m venv .venv || exit 1
.venv/bin/python -m pip install -r requirements.txt || exit 1
if [ ! -f panel-account.json ]; then
  .venv/bin/python configurer_compte.py || exit 1
fi
.venv/bin/python main.py
read '?Appuie sur Entrée pour fermer.'
