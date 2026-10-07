#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
learning_python=python3
if [[ -x .venv/bin/python ]]; then
    learning_python=.venv/bin/python
fi
"$learning_python" -m flask --app app init-db
if [[ "${PAYMENT_MODE:-demo}" == demo ]]; then
    "$learning_python" -m flask --app app seed-demo
fi
exec "$learning_python" -m gunicorn --workers "${WEB_CONCURRENCY:-2}" --bind "0.0.0.0:${PORT:-8000}" --access-logfile - app:app
