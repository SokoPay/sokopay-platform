#!/usr/bin/env bash
# SokoPay demo server (Git Bash / macOS / Linux). See scripts/demo.ps1 for details.
set -euo pipefail
export DJANGO_SETTINGS_MODULE=config.settings.dev DATABASE_URL=sqlite:///demo.sqlite3 \
       SOKOPAY_ACTIVE_LICENCE=DEMI ALLOW_MOCK_INTEGRATIONS=True ALLOWED_HOSTS=localhost,127.0.0.1,10.0.2.2
PY=${PY:-./.venv/Scripts/python.exe}
[ -x "$PY" ] || PY=./.venv/bin/python
rm -f demo.sqlite3
"$PY" manage.py migrate -v 0
"$PY" manage.py seed_demo --out demo-credentials.md
echo "Logins saved to demo-credentials.md. Starting the server on port 8000..."
"$PY" manage.py runserver 0.0.0.0:8000
