# Engineering operations: CI, deployment, monitoring and DR

## Pilot test server

For a full end-to-end test on one DigitalOcean droplet, see **DEPLOY-DIGITALOCEAN.md** (PDF: `SokoPay-DigitalOcean-Deployment-Guide.pdf`). It uses the files in `deploy/`: Docker Compose, Caddy HTTPS, the `.env` template, and the backup, update and wipe scripts.

The server runs `config.settings.staging`, which has production hardening plus the mock payment partner. Production uses `config.settings.prod`. The hardening shared by both lives in `config/settings/hardened.py`.

## Continuous integration (`.github/workflows/ci.yml`)

These checks run on every pull request and every push to main:

| Job | Checks |
|---|---|
| Backend | ruff, bandit (no medium or high findings), pip-audit, `manage.py check`, missing migrations, and the full test suite against **PostgreSQL 16** with coverage of at least 80%. It also confirms production settings refuse a mock rail. |
| Mobile | `flutter analyze` and `flutter test` for the shared package and all three apps. |
| Infra | `terraform fmt -check` and `terraform validate`, with no AWS access. |
| Image | `docker build` of `backend/Dockerfile` plus a Trivy scan that fails on critical vulnerabilities. |

The Postgres-only tests in `apps/ledger/tests/test_concurrency_postgres.py` race real threads against the same money. They cover a double spend, a repeated idempotency key, a double refund, and a double cash-out approval. They are skipped on SQLite.

The first run on Postgres found three bugs that SQLite had hidden. All three are now fixed and covered by tests:

1. **Ledger deadlock.** Several transactions using a new account at once could deadlock or fail. Balance rows are now created before any lock, and locks are taken in one statement in a fixed order.
2. **Refunds failed on Postgres.** Every refund failed, because a row lock was combined with a join to an optional customer. Row locks now apply to the locked table only.
3. **Large-transaction record crashed.** Its key was longer than its 64-character column, so the record crashed on Postgres.

## Deployment (`.github/workflows/deploy.yml`)

Deployment is manual only, to staging or production. Production is a protected GitHub environment, so a second person must approve.

1. Build the image tagged with the commit and push it to ECR.
2. Run `terraform plan` with the new image.
3. Run `manage.py migrate` as a one-off ECS task on the new image. If it fails, nothing rolls out.
4. Run `terraform apply` of the reviewed plan.
5. Wait until the services are stable.

AWS access uses OpenID Connect, with the `AWS_DEPLOY_ROLE_ARN` environment secret and no long-lived keys. Each environment needs its own `<env>.tfvars` file and remote state.

**[VERIFY]** Nothing has been applied to AWS yet.

## Monitoring (`infra/monitoring.tf`, `apps/common/health.py`)

**Probes.** `/healthz` is the liveness probe and `/readyz` is the readiness probe, which checks the database and cache.

**Logs.** Production logs one JSON object per line.

**Ops snapshot.** Every 5 minutes the app logs one ops snapshot line covering:
- stuck payouts and refunds;
- transfers, cross-border sends and orders pending over an hour;
- failed payments;
- abandoned webhooks;
- open high-severity AML alerts;
- reconciliation breaks;
- overdue disputes;
- waiting withdrawals;
- safeguarding status.

**Alarms.** Each field becomes a CloudWatch metric with an alarm, and all alarms go to one SNS topic (`var.alert_emails`). There are also alarms for:
- a missing snapshot, which means the worker or beat is down;
- CRITICAL log lines, and error spikes;
- API 5xx errors, p95 latency and unhealthy targets;
- ECS CPU and memory;
- the beat scheduler not running;
- RDS CPU, storage, memory and connections;
- Redis memory.

**Dashboard.** One dashboard is called `<name>-operations`.

**Health page.** Staff see the same numbers in the portal under Health.

## Backup and restore drill (`backend/scripts/dr_drill.sh`)

The drill works in four steps:

1. Create a fresh database and fill it with real money flows (`scripts/dr_drill_seed.py`).
2. Fingerprint it with `manage.py db_fingerprint --out`.
3. Back it up with `pg_dump -Fc`, then restore it into a fresh database.
4. Compare with `db_fingerprint --compare`.

The comparison checks row counts, a SHA-256 checksum over every account balance, and the last journal entry. It also checks that the ledger sums to zero and that cached balances equal the postings. It exits non-zero on any difference.

### Drill run, 2026-10-08 (local PostgreSQL 16)

The drill passed with no differences.

| Item | Result |
|---|---|
| Data | 42 users, 44 ledger accounts, 139 journal entries, 318 postings, 40 payments, 8 refunds, 10 cash-outs |
| Backup size | 316 KB |
| Backup time | 1 s |
| Restore time | 2 s |
| Verification time | 10 s |
| Ledger sum | 0 |
| Integrity breaks | 0 |

### Production drill (quarterly)

1. Restore the latest RDS snapshot, or a point in time, to a NEW instance. Never overwrite the live one.
2. Point `DATABASE_URL` at the restored instance.
3. Run `manage.py db_fingerprint --compare <fingerprint taken at the snapshot time>`, or at least check that `ledger_sum` is 0 and integrity breaks are 0.
4. Record the times against the targets: RPO under 1 hour and RTO under 4 hours (see infra/README.md).
5. Delete the drill instance.

## Not yet done

- **Terraform:** `terraform validate` passes with Terraform 1.16.5 (2026-10-08). Nothing has been planned or applied to AWS yet.
- **Docker image:** it has not been built locally because Docker Desktop doesn't start on this machine. CI builds it.
- **Android APKs:** built 2026-10-08 (debug-signed, internal testing); see `mobile/dist/README.md`.
