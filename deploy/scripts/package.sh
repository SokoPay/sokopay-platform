#!/usr/bin/env bash
# Run on YOUR computer (Git Bash) from the sokopay-platform folder. Makes sokopay-deploy.tgz
# with only what the server needs: the backend source and the deploy folder. No virtualenv,
# local databases, .env files, demo logins or caches.
#   bash deploy/scripts/package.sh
#   scp sokopay-deploy.tgz deploy@<droplet-ip>:/opt/sokopay/
set -euo pipefail
cd "$(dirname "$0")/../.."
tar czf sokopay-deploy.tgz \
  --exclude='backend/.venv' --exclude='*/__pycache__' --exclude='*.pyc' --exclude='*.sqlite3' \
  --exclude='backend/.env' --exclude='deploy/.env' --exclude='backend/demo-credentials.md' \
  --exclude='backend/staticfiles' --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='deploy/backups' \
  backend deploy
echo "Created sokopay-deploy.tgz ($(du -h sokopay-deploy.tgz | cut -f1))"
