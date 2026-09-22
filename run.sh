#!/usr/bin/env bash
# LLM Health Assistant - one-command start (macOS / Linux)
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
PY=.venv/bin/python
$PY -m pip install --disable-pip-version-check -q -r requirements.txt
$PY scripts/setup_env.py
$PY manage.py migrate --noinput -v 0
$PY manage.py seed_dataset
$PY manage.py seed_models --repair
$PY manage.py ensure_admin
echo "App running at http://127.0.0.1:8000 (Ctrl+C to stop)"
$PY manage.py runserver 127.0.0.1:8000
