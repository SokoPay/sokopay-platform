#!/usr/bin/env bash
# Deploy the latest version: pull from GitHub, back up the database, rebuild the image,
# run migrations, restart SokoPay's containers (only SokoPay's), check health.
#   cd /opt/sokopay/deploy && bash scripts/update.sh            # latest main
#   BRANCH=release-1 bash scripts/update.sh                     # another branch or tag
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d ../.git ]; then
  git -C .. fetch --prune origin
  git -C .. checkout "${BRANCH:-main}"
  git -C .. pull --ff-only origin "${BRANCH:-main}"
  echo "Now at: $(git -C .. log -1 --format='%h %s')"
fi
if [ -n "$(docker compose ps db --status running -q 2>/dev/null)" ]; then
  bash scripts/backup.sh
fi
docker compose build web
docker compose run --rm web python manage.py migrate --noinput
docker compose up -d
sleep 8
docker compose ps
curl -fsS "http://127.0.0.1:$(grep '^SOKOPAY_WEB_PORT=' .env | cut -d= -f2 || echo 8100)/readyz" && echo " <- ready"
