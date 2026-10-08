#!/usr/bin/env bash
# Deploy a new version uploaded to /opt/sokopay (see docs/DEPLOY-DIGITALOCEAN.md, section 9):
# back up, rebuild the image, run migrations, restart, check health.
set -euo pipefail
cd "$(dirname "$0")/.."
if docker compose ps db --status running -q >/dev/null 2>&1 && [ -n "$(docker compose ps db --status running -q)" ]; then
  bash scripts/backup.sh
fi
docker compose build web
docker compose run --rm web python manage.py migrate --noinput
docker compose up -d
sleep 8
docker compose ps
curl -fsS "https://$(grep '^SOKOPAY_DOMAIN=' .env | cut -d= -f2)/readyz" && echo " <- ready"
