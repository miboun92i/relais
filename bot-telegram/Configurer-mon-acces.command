#!/bin/zsh
cd "$(dirname "$0")" || exit 1
umask 077
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3 est nécessaire pour configurer le compte.'
  read '?Appuie sur Entrée pour fermer.'
  exit 1
fi
python3 configurer_compte.py
read '?Appuie sur Entrée pour fermer.'
