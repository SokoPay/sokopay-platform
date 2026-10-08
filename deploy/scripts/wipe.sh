#!/usr/bin/env bash
# PERMANENTLY delete all SokoPay data on this server: database, Redis, uploaded files,
# certificates, backups, the .env secrets and the built images. Use after the test.
# Then destroy the droplet in the DigitalOcean control panel (docs, section 11).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "This deletes ALL SokoPay data on $(hostname): database, backups, secrets."
read -r -p 'Type WIPE to continue: ' answer
[ "$answer" = "WIPE" ] || { echo "Cancelled."; exit 1; }
docker compose --profile localdb down -v --remove-orphans
rm -rf backups
shred -u .env 2>/dev/null || rm -f .env
docker image rm -f sokopay-backend:local 2>/dev/null || true
docker system prune -af --volumes
crontab -l 2>/dev/null | grep -v 'scripts/backup.sh' | crontab - || true
echo "Done. Remaining SokoPay volumes: $(docker volume ls -q | grep -c sokopay || true) (should be 0)."
echo "Next: destroy the droplet (and any snapshots, backups, managed database, DNS records) in DigitalOcean."
