#!/usr/bin/env bash
# Database backup (PostgreSQL custom format) into deploy/backups, keeping the last 14.
# Daily at 02:00:   (crontab -l; echo "0 2 * * * cd /opt/sokopay/deploy && bash scripts/backup.sh") | crontab -
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups && chmod 700 backups
f="backups/sokopay-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U sokopay -d sokopay -Fc > "$f"
chmod 600 "$f"
ls -1t backups/*.dump | tail -n +15 | xargs -r rm -f
echo "Backup written: $f ($(du -h "$f" | cut -f1))"
