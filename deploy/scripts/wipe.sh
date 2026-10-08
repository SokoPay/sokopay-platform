#!/usr/bin/env bash
# PERMANENTLY delete SokoPay's data from this droplet, and ONLY SokoPay's:
# its containers, database and Redis volumes, certificates volume, backups, .env secrets,
# its built image and its backup cron job. Other projects on the droplet are NOT touched:
# no global "docker system prune", nothing outside the "sokopay" Compose project.
# Afterwards follow docs/DEPLOY-DIGITALOCEAN.md section 13 (proxy config, DNS, deploy key).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "This deletes ALL SokoPay data on $(hostname): database, Redis, backups, secrets."
echo "Other projects on this droplet are left alone."
docker compose --profile localdb --profile caddy ps
read -r -p 'Type WIPE SOKOPAY to continue: ' answer
[ "$answer" = "WIPE SOKOPAY" ] || { echo "Cancelled."; exit 1; }

# Containers, networks and the project's named volumes (pgdata, redisdata, caddy_*),
# plus the image this project built. --rmi local never removes shared images such as
# postgres:16 or redis:7 that another project might use.
docker compose --profile localdb --profile caddy down --volumes --remove-orphans --rmi local
# Belt and braces: any volume still labelled as belonging to the sokopay project.
docker volume ls -q --filter label=com.docker.compose.project=sokopay | xargs -r docker volume rm
docker image rm -f sokopay-backend:local 2>/dev/null || true

rm -rf backups
shred -u .env 2>/dev/null || rm -f .env
( crontab -l 2>/dev/null | grep -v 'sokopay/deploy && bash scripts/backup.sh' ) | crontab - || true

left=$(docker ps -a --filter label=com.docker.compose.project=sokopay -q | wc -l)
vols=$(docker volume ls -q --filter label=com.docker.compose.project=sokopay | wc -l)
echo "SokoPay containers left: $left, volumes left: $vols (both should be 0)."
echo "Other projects still running:"; docker ps --format '  {{.Names}}  ({{.Status}})'
echo "Next: remove the SokoPay reverse-proxy site, its certificate, DNS record, the GitHub"
echo "deploy key and /opt/sokopay (guide section 13)."
