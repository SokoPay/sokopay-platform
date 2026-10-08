# Platform review: governance, trust, traceability, auditability, manageability, resilience

**Date:** 2026-10-08.

**Scope:** everything built so far:
- the Django backend (22 apps);
- the merchant and staff portals;
- the customer, agent and business Flutter apps;
- USSD and hosted checkout;
- infrastructure as code;
- CI/CD;
- the legal documents.

**Method:** each pillar was checked against the code itself (not the design documents). Every control listed points to where it lives and to the automated test that proves it. Gaps found during the review were fixed where the fix was small and safe. Larger gaps are listed with a priority.

## Scorecard

| Pillar | Rating | In one line |
|---|---|---|
| [Governance](01-governance.md) | Adequate, improving | Strong maker-checker and role separation in the software. Missing the human side: policy owners, a risk register, and named sign-off for the 168 [VERIFY] items. |
| [Trust](02-trust.md) | Strong for software, open for operations | 27 security findings fixed. Encryption, PIN and 2FA, SIM-swap defence, and no tipping-off. Pentest, store signing and legal sign-off are outstanding. |
| [Traceability](03-traceability.md) | Strong | Every request, ledger entry and audit event now shares one request ID. Every payment has a public reference, and every partner callback is stored. |
| [Auditability](04-auditability.md) | Strong | Append-only ledger and an append-only audit log (database-enforced on Postgres). Four-eyes on money and reports. A proven backup fingerprint. |
| [Manageability](05-manageability.md) | Adequate | Role-based back office, a health page, alarms, CI/CD and runbooks. Configuration is spread across many environment variables, and staff accounts are managed only in Django admin. |
| [Resilience](06-resilience.md) | Adequate, with known single points | Money flows survive partner timeouts, crashes and queue outages, and locking is proven on Postgres. The single rail partner (your decision), Redis, and the scheduler remain single points. |

## Fixed during this review

| # | Pillar | Fix | Test |
|---|---|---|---|
| R1 | Auditability, governance | **Append-only audit log** (`AuditEvent`) for about 40 privileged actions, including KYC holds, AML case work, STRs, prices, float, refunds, disputes, settlements, bulk approvals, API keys, webhooks, merchant approval, 2FA resets, exports and support sign-outs. On PostgreSQL a database trigger blocks UPDATE and DELETE. Searchable and exportable on the staff **Audit** page, and the export itself is logged. | `apps/common/tests/test_audit.py` |
| R2 | Governance | **Staff role grants and privilege changes** (`is_superuser`, `is_staff`, `is_active`, user type) are audited wherever they are made, including Django admin and the shell. | same |
| R3 | Traceability | **Request ID** on every request (`X-Request-ID`): it is returned to the caller, put on every production log line, and stored on every audit event and **every ledger entry**. The apps send their own ID with each call. | same |
| R4 | Resilience | A **message-queue outage after a commit** no longer turns into an error for the customer, which could cause a repeat payment. Post-commit queueing is non-fatal. | `apps/notifications/tests/test_queue_outage.py` |
| R5 | Resilience | **Bulk batches whose start was lost** are restarted automatically by the 5-minute poller. This is safe to re-run. | bulk tests |
| R6 | Trust | Two app screens used the screen's context after an await, which could crash on a slow network. Both are fixed. | `flutter analyze` clean |

## Top 10 actions before launch

1. **Legal sign-off:** Ghanaian counsel and the DPO review the six drafts in `docs/legal`. Register with the Data Protection Commission and confirm offshore hosting (AWS Cape Town) is acceptable, or plan in-country hosting.
2. **Owner for the [VERIFY] register:** 168 items across code and docs, covering tariffs, limits, partner specs and regulatory times. Each needs an owner and a dated decision (governance).
3. **External penetration test**, mobile included (trust).
4. **Play Store signing key:** an upload keystore kept out of the repository and away from developers. Today's APKs are debug-signed and for internal testing only (trust).
5. **Break-glass superusers only:** no day-to-day superuser accounts, quarterly access review using the audit log (governance).
6. **Ship the audit log to write-once storage:** S3 Object Lock, retained for at least 5 years (auditability).
7. **Apply the Terraform in staging.** It is validated, with Terraform 1.16.5. Then run the RDS restore drill there (resilience, manageability).
8. **Business continuity plan for the single rail partner:** what customers see, and the manual steps, if the partner is down. No automatic failover, by your decision (resilience).
9. **Load test** the payment paths at expected peak, salary day and month end (resilience).
10. **Staff onboarding and offboarding procedure** using the role groups, with a joiner/mover/leaver checklist (governance, manageability).
