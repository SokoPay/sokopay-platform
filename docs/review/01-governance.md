# 1. Governance

Governance means the right people decide, nobody decides alone on money or risk, and the rules the business agreed are the rules the software enforces.

## Controls in place

| Control | Where | Evidence |
|---|---|---|
| **Licence gate:** every regulated feature is switched on only by the active Bank of Ghana licence | `apps/licensing` (capability matrix) | licensing tests and per-feature "blocked under PSP Standard" tests |
| **Role separation for staff:** operations, compliance, finance and support see only their pages | `apps/portal/decorators.py` (`staff_role_required`) | `test_ops_portal.py`, `test_compliance_portal.py`, `test_health.py` |
| **Role separation for merchants:** Owner, Admin, Finance, Cashier, Developer. Only Owner or Finance move money, with the PIN or 2FA again | `apps/merchants/app_views.py`, portal views | merchant app and portal tests |
| **Maker-checker:** a second person approves settlements over the threshold or to new accounts, agent float top-ups, fee and commission changes, STR filing, and bulk payouts (sole approver recorded) | settlement, agents, pricing, compliance reports, bulk | tests for each (maker can't approve own) |
| **Dispute decisions** need a written reason, and staff can decide only after the merchant's deadline | `apps/merchants/disputes.py` | `test_refunds_disputes.py` |
| **Prices can't be back-dated** and approved prices can't be edited; there are typo caps | `apps/pricing` | `test_pricing.py` |
| **AML holds** can be released only from the case, never from the general KYC page | `views_ops.kyc_overview` | `test_ops_portal.py` |
| **Change control:** CI on every change; production deploy is manual and needs a second approver (GitHub environment) | `.github/workflows` | CI configuration |
| **Role grants and privilege changes are audited** *(new, R2)* | `apps/common/signals.py` | `test_audit.py` |
| **Written policies:** privacy, terms, merchant and agent terms, complaints, acceptable use | `docs/legal` | legal page tests |

## Gaps and actions

| Priority | Gap | Action | Owner |
|---|---|---|---|
| High | **168 [VERIFY] items** with no owner or decision log: tariffs, limits, partner specs, regulatory timelines, legal facts | Create a decision register (one row per item: owner, decision, date, source). Close it before launch. | CEO / Compliance |
| High | **Superusers bypass role checks** (they still can't approve their own maker-checker items, because that rule compares users) | Use superuser accounts for break-glass only. Review grants monthly from the audit log (`role.add`, `user.privilege_change`). | CTO |
| Medium | **Staff accounts are created in Django admin** with no in-app approval step | Add a staff joiner/leaver procedure. Later, a portal page where adding a role needs a second approver. | Operations |
| Medium | **Bulk payouts allow self-approval when a merchant has only one approver** (recorded and logged) | Decide a policy, for example a cap for self-approved batches, and set it per merchant risk tier. | Risk |
| Medium | **Licence and partner switches are environment settings** (`SOKOPAY_ACTIVE_LICENCE`, `RAIL_PROVIDER`, `CROSS_BORDER_PROVIDER` and others) | Governed through deploy approval today. Record each change in the decision register, and have the deploy job post a change note. | CTO |
| Low | No risk register, model-risk document for the AML rules, or board reporting pack | The regulatory figures page gives the numbers. Add a quarterly risk and compliance report template. | Compliance |
