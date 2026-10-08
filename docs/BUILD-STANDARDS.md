# Build Standards

The rules every contributor follows. They exist because this is money and because the
previous codebase failed on most of them (see `../../docs/11-Codebase-Review-2020-2024.md`).

## Non-negotiables

1. **No secrets in the repository — ever.** No passwords, API keys, private keys,
   tokens or database dumps. Everything secret comes from environment variables.
   `.gitignore` blocks the obvious files; if a secret appears in a diff, stop and
   remove it before committing, then rotate it.
2. **Money is integer minor units (pesewas). No floats, anywhere, for money.** Use the
   `Money` type (`apps/common/money.py`).
3. **All money movement goes through `ledger.services.post_entry()`.** Never write to
   `Posting` or balances directly. Never store a single mutable balance as the truth.
4. **Money-moving code is idempotent.** Anything that can be retried (API POSTs,
   provider callbacks) carries an idempotency key.
5. **Two-person rule on the money core.** Changes to `ledger`, `rails` and
   `settlements` require review by a second engineer before merge.
6. **Verify, re-query, don't trust a callback blindly.** Provider callbacks are
   signature-checked AND confirmed by a status query before we act on them.

## Code style

- Python formatted and linted with **ruff** (`ruff check` and `ruff format`). CI fails
  on lint errors.
- Type hints on public functions. Clear names over cleverness.
- Comments explain **why**, not what. Public modules and non-obvious logic get a
  docstring a newcomer can follow. (A product goal: the code is readable by technical
  reviewers and documented well enough for non-technical stakeholders to follow the
  intent.)
- No `print()` for logging — use the logging framework, and never log PII, PANs, PINs
  or secrets. (The old code printed full request payloads with phone numbers.)

## Testing

- **pytest** for everything. The `ledger` and `licensing` suites are the ones that
  matter most and must stay green.
- Target coverage: ≥ 90% on `ledger`, `payments`, `settlements`, `rails`; ≥ 70%
  overall.
- **Property-based tests (Hypothesis)** guard the ledger invariants.
- Money logic is tested for: duplicate callbacks, out-of-order callbacks, retries,
  insufficient funds, rounding edges, and concurrency (the concurrency tests run
  against **PostgreSQL**, because SQLite does not enforce `SELECT … FOR UPDATE`).

## Security

- Passwords hashed with **Argon2**. Staff and merchant-portal logins require **2FA**.
- **PCI scope minimised:** SokoPay never handles raw card numbers (PANs). Card entry
  uses the partner's hosted fields/redirect, keeping us at the lightest PCI scope.
- PII at rest (Ghana Card number, bank account numbers) is **field-encrypted**.
- UUID primary keys on anything exposed in a URL/API (no enumerable integer ids).
- Production settings enforce HTTPS, HSTS, secure cookies, and an explicit host
  allow-list; `DEBUG` is never on.
- CI runs **bandit** (SAST) and **pip-audit** (dependency CVEs). High/critical findings
  block the merge.

## Database

- **PostgreSQL** in every environment that moves money.
- Migrations are reviewed like code. No destructive migration without a backup plan.
- In production, the application database role has **no UPDATE/DELETE** on the ledger
  posting, journal-entry and audit-log tables — they are append-only at the permission
  level, not just by convention.

## Git

- Feature branches + pull requests. No direct commits to `main`.
- Commits are small and describe intent.
- CI must be green (lint, tests, bandit, pip-audit) before merge.
- **Do not push to GitHub without the product owner's say-so** (current project rule).

## Definition of done (for a money feature)

- [ ] Capability-gated via `require_capability(...)`.
- [ ] All writes through `post_entry()`; idempotent.
- [ ] Unit + integration tests, including failure and retry paths.
- [ ] Ledger invariants still hold (sum == 0, cached == recomputed).
- [ ] ruff, bandit, pip-audit clean.
- [ ] Reviewed by a second engineer (two-person rule for the core).
- [ ] No secrets or PII in code, logs or tests.
