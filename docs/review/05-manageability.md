# 5. Manageability

Manageability means a small operations team can run, configure, monitor, change and support the platform day to day without engineers, and engineers can change it safely.

## Controls in place

**Back office**

The staff portal, organised by role, covers:
- agents, float approvals and cash-outs;
- KYC holds;
- compliance cases, screening, reports and watchlists;
- reconciliation;
- support lookup;
- disputes;
- provider withdrawals;
- regulatory returns;
- prices;
- health;
- the audit log.

**Merchant self-service**

In both the portal and the app, merchants manage payments, refunds, disputes, settlements, payment links, webhooks, API keys, statements and their team. This keeps support load low.

**Configuration in one place per concern**

- Licence: `SOKOPAY_ACTIVE_LICENCE`.
- Partners: `RAIL_PROVIDER`, `CROSS_BORDER_PROVIDER`, `LIFESTYLE_PARTNERS`, `SMS_PROVIDER`.
- Prices: the Prices page, with maker-checker.
- AML thresholds: `AML_RULES`.
- Limits: `KYC_TIER_LIMITS`.

**Observability**

- `/healthz` and `/readyz`.
- A 5-minute ops snapshot, shown on the Health page and sent to CloudWatch alarms.
- JSON logs with request IDs.
- One operations dashboard.

**Delivery**

- CI covers lint, a security scan, a dependency audit, tests on Postgres, Flutter analysis and tests, Terraform validation and the image build.
- Deploys are manual and approved, with migrations run before traffic moves.
- Infrastructure as code: Terraform for VPC, ECS, RDS, Redis, WAF, alarms and DR.

**Documentation**

Architecture, build standards, licence matrix, connectors, rails, bulk, DEMI operations, AML, operations, engineering operations, security review, legal, and this review.

## Gaps and actions

| Priority | Gap | Action |
|---|---|---|
| High | **Terraform is validated but never applied** (`terraform validate` passed with Terraform 1.16.5 on 2026-10-08; formatting fixed) | Apply to a staging AWS account (needs AWS access and approval), then run the RDS restore drill there. |
| Medium | **Configuration is spread across about 40 environment variables** with no single view | Add a read-only Configuration page for staff showing the active licence, partners, flags and limits (no secrets). Generate the list from settings so it can't drift. |
| Medium | **Staff user management happens in Django admin** | Add a portal page for joiners, movers and leavers with role changes audited (already) and second-person approval for privileged roles. |
| Medium | **No runbooks** for common incidents: rail partner down, stuck payouts, safeguarding shortfall, Redis down, a compromised account | Write one page per alarm in `docs/runbooks/`, linked from each CloudWatch alarm description. |
| Low | **Mobile builds need a lot of disk** (the NDK alone is 2.2 GB); local builds failed for lack of space | Build APKs in CI (the job is added in `ci.yml`), not on personal machines. |
| Low | **Many apps (22)** in one Django project | Fine at this size. Keep the app boundaries (the ledger depends only on the small common app) as the team grows. |
