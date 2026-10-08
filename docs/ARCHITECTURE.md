# Architecture

A short map of how SokoPay is put together and why. Written for engineers and for
reviewers (Ricky, Gideon) who want to judge the design quickly.

## Shape

One **modular monolith** on Django, not microservices. For a small team moving money,
a single deployable with clear internal module boundaries is safer and cheaper than a
fleet of services, and it removes a whole class of "money lost between services" bugs.
We can extract a service later if real load demands it. (The old `sokopay_sub` branch
split into services prematurely; we are not repeating that yet.)

```
  Flutter apps (customer, agent)  ─┐
  Merchant portal (HTMX)           ├─►  CloudFront + WAF ─► ALB ─► Django (ECS Fargate)
  Admin portal (HTMX)              │                                   │
  Hosted checkout                  │                                   ├─► PostgreSQL (RDS, Multi-AZ)
  Merchant servers (REST API)     ─┘                                   ├─► Redis (ElastiCache)
                                                                       └─► Celery workers (SQS)
                                                                             │
                                                        RailProvider adapters │ (Korba / Nsano / … / mock)
```

Region: **AWS af-south-1 (Cape Town)** — the nearest AWS region to Ghana, chosen for
data-residency defensibility with BoG and the Data Protection Act.

## Modules (Django apps)

| App | Responsibility | Built? |
|---|---|:--:|
| `common` | Shared base model (UUID + timestamps), the `Money` value type | ✅ |
| `accounts` | Custom user (phone-based), roles, lockout | ✅ (minimal) |
| `licensing` | The licence-capability gate (build-all, roll-out-per-licence) | ✅ |
| `ledger` | Double-entry ledger — the financial core | ✅ |
| `rails` | `RailProvider` interface + Korba/Nsano/mock adapters | ▶ next |
| `payments` | Collections, bill pay, airtime/data, checkout, webhooks | ▶ next |
| `merchants` | Merchant onboarding/KYB, settlement, API keys | later |
| `agents` | Agent onboarding, float, cash-in/out (DEMI) | later |
| `compliance` | KYC tiers, limits, fraud rules, sanctions, STR export | later |
| `portal` | Merchant + admin HTMX portals, maker-checker | later |

## Three ideas the whole design rests on

### 1. The ledger is the source of truth
Every movement of money is a balanced, append-only double-entry journal in integer
pesewas. Balances are derived from postings, never hand-edited. All writes go through
one function, `ledger.services.post_entry()`, which validates balance, enforces
currency, locks rows, and supports idempotency keys. Nothing else may write to the
ledger. See `backend/apps/ledger/models.py` for the sign convention.

### 2. The licence gate makes "build everything now" safe
Regulated activities are named capabilities. The active licence resolves to a set of
allowed capabilities. Feature code calls `require_capability(...)` before doing
anything regulated. So we can merge wallet and agent code today; it simply refuses to
run until the DEMI licence flips it on. See `docs/LICENCE-CAPABILITY-MATRIX.md`.

### 3. Rails are swappable
Every connection to an Enhanced PSP (Korba, Nsano) or scheme sits behind one
`RailProvider` interface, with a `mock` implementation for tests and local dev.
SokoPay runs on **one** partner at a time — Korba *or* Nsano, never both, no
fail-over. The shared interface means the choice (or a later switch) is configuration
(`RAIL_PROVIDER`), not a rewrite.

## Safety properties (and how we get them)

| Property | Mechanism |
|---|---|
| No lost or invented money | Double-entry ledger; whole-ledger sum == 0 is tested |
| No double-charge on retries/duplicate callbacks | Idempotency keys on `post_entry` |
| No double-spend under concurrency | `SELECT … FOR UPDATE` on balance rows, ordered by id (PostgreSQL) |
| No illegal activity for our licence | The licence gate |
| No secrets in code | All secrets from environment; `.gitignore`; see BUILD-STANDARDS |
| PII protected | Field-level encryption for Ghana Card / account numbers; UUID keys |
| Tamper-evident operations | Append-only ledger + append-only audit log (later); read-only admin |

## What is deliberately **not** here yet

USSD, POS hardware, cross-border settlement, FX, ML fraud scoring, multi-currency
operations. All are anticipated in the design (capabilities, multi-currency ledger)
but out of the current build scope to protect the PSP-Standard launch timeline.
