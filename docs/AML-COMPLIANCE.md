# Fraud & AML monitoring

**Who this is for:** the MLRO / compliance team (how to work it) and engineers (how it
works). Code: `backend/apps/compliance/`; back-office: **Compliance** tab (staff in the
`compliance` group only).

> **Tipping-off:** never tell a customer that an alert, investigation or report exists.
> The app only ever says "Your wallet is on hold. Please contact SokoPay support."

## What is watched, and when

Every movement of money goes through one ledger function; a post-commit hook evaluates
every committed entry (never one that rolled back, and never slowing a payment).

| Rule | Severity | Fires when (defaults — tune in `AML_RULES`) |
|---|---|---|
| `LARGE_TXN` | record | a wallet movement ≥ GH₵50,000 → a *large-transaction report* row for the FIC |
| `VELOCITY_OUT` | medium | ≥ 10 payments out of one wallet within 60 minutes |
| `STRUCTURING` | medium | ≥ 3 movements in 24 h each 80–100% of GH₵10,000 (splitting to stay under a line) |
| `PASS_THROUGH` | **high** | ≥ GH₵2,000 in and ≥ 80% of it out within 2 h, wallet left near-empty (mule) |
| `MANY_SENDERS` | **high** | ≥ 10 different SokoPay users send to one wallet in 24 h |
| `NEW_ACCOUNT_HIGH_OUT` | medium | account < 7 days old sends ≥ GH₵5,000 in 24 h |
| `DORMANT_REACTIVATION` | medium | silent for 90 days, then sends ≥ GH₵2,000 |
| `NEW_DEVICE_HIGH_OUT` | medium | new phone signed in within 24 h, then ≥ GH₵2,000 out (possible takeover) |
| `AGENT_CASH_CYCLING` | medium | same agent does cash-in **and** cash-out for the same customer in 24 h (≥ GH₵1,000) |
| `AGENT_SPLIT_CASHOUT` | medium | ≥ 3 cash-outs for one customer at one agent in 24 h |
| `MERCHANT_SPIKE` | medium | daily batch (06:30): a merchant's day ≥ 5× its 30-day average and ≥ GH₵10,000 |
| `SANCTIONS_MATCH` | **high** + hold | customer/merchant name resembles a sanctions-list entry |
| `PEP_MATCH` | medium | resembles a politically exposed person → enhanced due diligence |

Refunds and reversals are ignored (they return money; they aren't customer activity).
A repeat of the same pattern on the same subject adds to the **existing** alert (hit
count + evidence) rather than opening a new one. **All thresholds are `[VERIFY]` with the
MLRO and current FIC guidance before go-live.**

## Sanctions & PEP screening

Load lists under **Compliance → Watchlists** (or `manage.py import_watchlist`):
UN consolidated XML, OFAC SDN CSV, or a simple CSV (name, aliases, dob, nationality,
reference, kind) for Ghana domestic / PEP lists. Nothing is fetched from the internet —
download official files yourself. An upload replaces that list and **rescreens everyone**.

Customers are screened when their Ghana Card name is verified, merchants when submitted
for review. Matching ignores case, accents, punctuation and word order and tolerates
small spelling differences (`AML_SCREENING_THRESHOLD`, default 0.88). Single names are
never screened (too many false hits). A **sanctions** hit holds the wallet automatically
until a compliance officer **confirms** (keeps hold, escalates) or **clears** it (releases,
closes) — with a written reason either way.

## Working an alert

Queue → open an alert: evidence, the customer's KYC, balance and last 30 days, and the
full timeline. Actions: assign to me · note · escalate · **hold / release wallet** · close
(reason required) · **draft STR**. Every action is recorded on the timeline.

## Reporting to the FIC

* **STR:** draft from an alert (narrative ≥ 50 characters: who, what, when, why) → a
  **second** compliance officer approves → mark filed with the FIC reference (closes the
  linked alerts as "reported"). Download as JSON to transcribe into goAML.
  `[VERIFY field mapping to the FIC goAML schema]`
* **Large transactions:** Reports → *Export new rows (.csv)*; exported rows are marked
  so nothing is filed twice.

## Staff roles

Back-office access is now role-based (Django groups created by migration):
`compliance` (this area), `operations`, `support`, `finance`. Superusers see everything.
Add staff to groups in Django admin.
