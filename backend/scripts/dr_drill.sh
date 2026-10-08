#!/usr/bin/env bash
# Backup-restore drill against a local / staging PostgreSQL. Proves that a backup taken
# with pg_dump restores into an identical, consistent database, and times it.
#
#   PGHOST=127.0.0.1 PGUSER=sokopay PGPASSWORD=... ./scripts/dr_drill.sh
#
# Steps: fresh source DB → migrate → drill data → fingerprint → pg_dump (custom format)
# → restore into a fresh DB → fingerprint --compare (row counts, balance checksum, last
# entry, ledger sums to zero, cached balances = postings) → report timings.
# For AWS: restore the latest RDS snapshot / point-in-time to a NEW instance, point
# DATABASE_URL at it and run only the last step (db_fingerprint --compare) — see
# docs/DR-DRILL.md.
set -euo pipefail

PY=${PY:-python}
PG_DUMP=${PG_DUMP:-pg_dump}
PG_RESTORE=${PG_RESTORE:-pg_restore}
PSQL=${PSQL:-psql}
HOST=${PGHOST:-127.0.0.1}
USER=${PGUSER:-sokopay}
SRC=sokopay_drill_src
DST=sokopay_drill_restored
WORK=${WORK:-$(mktemp -d)}
DUMP=${DUMP:-$WORK/sokopay.dump}          # where pg_dump writes (must be readable by pg_restore)
DU=${DU:-du -k}
url() { echo "postgres://${USER}:${PGPASSWORD}@${HOST}:5432/$1"; }

echo "== 1. fresh source database"
$PSQL -h "$HOST" -U "$USER" -d postgres -qc "DROP DATABASE IF EXISTS $SRC" -c "DROP DATABASE IF EXISTS $DST" -c "CREATE DATABASE $SRC"
DATABASE_URL=$(url $SRC) $PY manage.py migrate --noinput -v 0
DATABASE_URL=$(url $SRC) $PY manage.py shell -c "exec(open('scripts/dr_drill_seed.py').read())"
DATABASE_URL=$(url $SRC) $PY manage.py db_fingerprint --out "$WORK/before.json" > /dev/null

echo "== 2. backup"
t0=$(date +%s)
$PG_DUMP -h "$HOST" -U "$USER" -Fc -f "$DUMP" $SRC
t1=$(date +%s)

echo "== 3. restore into a fresh database"
$PSQL -h "$HOST" -U "$USER" -d postgres -qc "CREATE DATABASE $DST"
$PG_RESTORE -h "$HOST" -U "$USER" -d $DST --no-owner --exit-on-error "$DUMP"
t2=$(date +%s)

echo "== 4. verify the restored copy"
DATABASE_URL=$(url $DST) $PY manage.py migrate --check > /dev/null
DATABASE_URL=$(url $DST) $PY manage.py db_fingerprint --compare "$WORK/before.json" > "$WORK/after.json"
t3=$(date +%s)

SIZE=$($DU "$DUMP" | cut -f1)
echo "DRILL PASSED  backup ${SIZE} KB in $((t1 - t0))s, restore $((t2 - t1))s, verify $((t3 - t2))s"
echo "artefacts: $WORK"
