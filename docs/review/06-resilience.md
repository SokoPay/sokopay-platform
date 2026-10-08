# 6. Resilience

Resilience means that when a partner, a server or a network fails mid-payment, no money is lost or duplicated, the system recovers by itself where it safely can, and people are told when it can't.

## Controls in place

| Failure | What happens | Where | Evidence |
|---|---|---|---|
| Customer taps Pay twice, or a retry reaches us twice | Idempotency keys scoped per customer or merchant; ledger entries are idempotent | payments, wallet, ledger | idempotency tests |
| Two operations race for the same money | Row locks in a fixed order; balance rows created before locking (deadlock fixed); a wallet can't go negative | `apps/ledger/services.py` | **Postgres concurrency tests** (double spend, double refund, double cash-out approval) |
| Partner times out or the answer is unclear | Money is reserved before sending. An ambiguous outcome is treated as UNKNOWN, never FAILED, so it is never refunded while it might still arrive. Pollers re-query | settlements, refunds, interop, cross-border, lifestyle, bulk | payout and refund tests |
| Partner definitely refuses | Automatic reversal to the wallet or merchant balance | same | refund, cross-border and lifestyle tests |
| App or server crashes between reserving and sending | Item stays "processing" with no partner reference and is flagged as **stuck** after 10 minutes. It is never blindly re-sent | settlement, refunds | stuck-detection tests |
| Message queue (Redis) down after a commit *(fixed, R4)* | The committed action succeeds for the customer, the inbox still has the alert, and pollers pick up the work later | `on_commit(..., robust=True)` | `test_queue_outage.py` |
| Bulk batch start lost *(fixed, R5)* | Poller restarts it after 10 minutes; processing is safe to re-run | `apps/bulk/tasks.py` | bulk tests |
| Merchant webhook endpoint down | Retries over 24 hours, then the delivery is abandoned and visible; the endpoint switches off after 50 failures | `apps/merchants/webhooks.py` | `test_webhooks.py` |
| No push notification reaches the phone | Important alerts go by SMS instead | `apps/notifications` | `test_sms_fallback.py` |
| No smartphone or data | USSD menu, including cash-out approval | `apps/ussd` | `test_ussd.py` |
| Scheduler or worker dies | "ops snapshot missing" alarm and "beat not running" alarm | `infra/monitoring.tf` | n/a (alarms) |
| Database instance fails | RDS Multi-AZ; backups; restore drill **passed** with a fingerprint check | `infra/data_stores.tf`, `scripts/dr_drill.sh` | `test_db_fingerprint.py`, drill result |
| Region-wide outage | Cross-region DR is defined but **switched off** (`enable_dr = false`) pending a data-residency decision | `infra/dr.tf` | n/a |
| Traffic spike | API autoscaling on CPU and requests; WAF rate limits | `infra/autoscaling.tf`, `alb.tf` | n/a |

## Gaps and actions

| Priority | Gap | Action |
|---|---|---|
| High | **Single rail partner, with no failover by decision.** If Korba or Nsano is down, collections, payouts, bills and transfers to other networks stop | Business continuity plan: a status banner in the apps (switch from the back office), messages to customers and merchants, a partner SLA with credits, a manual settlement procedure, and a recovery checklist. Wallet-to-wallet, merchant QR from the wallet, and cash in and out at agents keep working because they don't touch the rail. |
| Medium | **Redis outage blocks sign-in** (one-time codes and rate limits live there; rate limits fail closed). Redis already runs Multi-AZ with automatic failover (`infra/data_stores.tf`) | Keep it. Add a runbook saying a Redis outage blocks sign-in and queueing but never corrupts money, and alarm on Redis failover events. |
| High | **No load test** | Load test at 3x expected salary-day peak: payments, P2P, cash-out approval and webhooks. Tune database connections and worker counts. |
| Medium | **Celery beat is a single task** (by design, to avoid double scheduling) | Covered by the two alarms. Add an automatic ECS restart policy and a runbook. |
| Medium | **Cross-region DR is off** | Decide with BoG and counsel whether a copy outside Ghana and South Africa is allowed. Then enable `enable_dr` and drill it. |
| Medium | **SMS provider is single** | Add a second SMS provider for one-time codes only (low risk, unlike payments). |
| Low | **No chaos testing** | After staging is up, run quarterly game-days: kill a worker, block the rail, fail over RDS. |
