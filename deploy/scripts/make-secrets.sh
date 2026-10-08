#!/usr/bin/env bash
# Fill every CHANGE_ME in deploy/.env with a strong random value (run once, on the server).
#   cd /opt/sokopay/deploy && bash scripts/make-secrets.sh
# Uses Docker's Python image, so nothing extra needs installing. Keeps existing values.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
chmod 600 .env
gen() { docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe($1))"; }
fernet() { docker run --rm python:3.12-slim python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }
set_var() {   # set_var NAME VALUE   (only if currently CHANGE_ME)
  if grep -q "^$1=CHANGE_ME$" .env; then
    sed -i "s|^$1=CHANGE_ME$|$1=$2|" .env; echo "  set $1"
  fi
}
PG=$(gen 24 | tr -d '_-'); RD=$(gen 24 | tr -d '_-')
set_var SECRET_KEY "$(gen 50)"
set_var FIELD_ENCRYPTION_KEY "$(fernet)"
set_var POSTGRES_PASSWORD "$PG"
set_var REDIS_PASSWORD "$RD"
set_var USSD_SHARED_SECRET "$(gen 32)"
set_var RAIL_WEBHOOK_SECRET "$(gen 32)"
# The connection strings embed the same passwords.
PGV=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-); RDV=$(grep '^REDIS_PASSWORD=' .env | cut -d= -f2-)
sed -i "s|^DATABASE_URL=postgres://sokopay:CHANGE_ME@db:5432/sokopay$|DATABASE_URL=postgres://sokopay:${PGV}@db:5432/sokopay|" .env
sed -i "s|^REDIS_URL=redis://:CHANGE_ME@redis:6379/0$|REDIS_URL=redis://:${RDV}@redis:6379/0|" .env
if grep -q CHANGE_ME .env; then echo "Still to fill in by hand:"; grep -n CHANGE_ME .env; else echo "All secrets set."; fi
echo "Now edit .env for SOKOPAY_DOMAIN, ACME_EMAIL, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, QR_BASE_URL."
