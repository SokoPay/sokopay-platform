#!/usr/bin/env bash
# Backup into deploy/backups, keeping the last 14 of each:
#   * the database (PostgreSQL custom format, .dump)
#   * uploaded KYB/ID documents (.media.tgz; already encrypted with FIELD_ENCRYPTION_KEY)
# Daily at 02:00:   (crontab -l; echo "0 2 * * * cd /opt/sokopay/deploy && bash scripts/backup.sh") | crontab -
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups && chmod 700 backups
ts="$(date +%Y%m%d-%H%M%S)"
f="backups/sokopay-$ts.dump"
docker compose exec -T db pg_dump -U sokopay -d sokopay -Fc > "$f"
chmod 600 "$f"
m="backups/sokopay-$ts.media.tgz"
if docker compose exec -T web sh -c 'test -d /app/media && tar -C /app/media -czf - .' > "$m" 2>/dev/null; then
  chmod 600 "$m"
else
  rm -f "$m"; m=""
  echo "Note: no uploaded documents to back up (none yet, or the web container isn't running)."
fi
# Keep the newest 14 of each. "|| true": a missing kind (e.g. no document backups yet) is not an error.
ls -1t backups/*.dump 2>/dev/null | tail -n +15 | xargs -r rm -f || true
ls -1t backups/*.media.tgz 2>/dev/null | tail -n +15 | xargs -r rm -f || true
echo "Backup written: $f ($(du -h "$f" | cut -f1))${m:+ and $m}"
