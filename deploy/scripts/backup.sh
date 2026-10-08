#!/usr/bin/env bash
# Backup into deploy/backups, keeping the last 14 of each:
#   * the database (PostgreSQL custom format, .dump)
#   * uploaded KYB documents (.media.tgz; already encrypted with FIELD_ENCRYPTION_KEY)
# Daily at 02:00:   (crontab -l; echo "0 2 * * * cd /opt/sokopay/deploy && bash scripts/backup.sh") | crontab -
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups && chmod 700 backups
ts="$(date +%Y%m%d-%H%M%S)"
f="backups/sokopay-$ts.dump"
docker compose exec -T db pg_dump -U sokopay -d sokopay -Fc > "$f"
chmod 600 "$f"
m="backups/sokopay-$ts.media.tgz"
if docker compose exec -T web tar -C /app/media -czf - . > "$m" 2>/dev/null; then
  chmod 600 "$m"
else
  rm -f "$m"; m=""; echo "Note: web isn't running, so documents weren't backed up this time."
fi
ls -1t backups/*.dump 2>/dev/null | tail -n +15 | xargs -r rm -f
ls -1t backups/*.media.tgz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "Backup written: $f ($(du -h "$f" | cut -f1))${m:+ and $m}"
