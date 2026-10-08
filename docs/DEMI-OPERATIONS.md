# DEMI operations guide

For compliance, finance, operations — and the engineers supporting them. Everything
here is built and switched **off** until the Bank of Ghana grants the DEMI licence
(`SOKOPAY_ACTIVE_LICENCE=DEMI`). Under PSP licences these features refuse to run.

## What DEMI unlocks

| Feature | What the customer sees | Built in |
|---|---|---|
| Wallet | Add money (MoMo), balance, activity | `apps/wallet` |
| Send to SokoPay users | Instant wallet-to-wallet | `apps/wallet` |
| Send to other wallets & banks | MoMo, banks (GIP), G-Money, Zeepay… | `apps/wallet/interop.py` |
| Pay from the wallet | Bills, airtime, data — no MoMo prompt | `apps/payments` |
| Agents | Cash in / cash out at an agent | `apps/agents` |
| Insurance & loans in the wallet | Pay premiums from it; receive loans into it | `apps/marketplace` |
| Remittances from abroad | Money arrives in the wallet | `apps/wallet/remittance.py` |

## 1. KYC tiers and limits

Every customer has a KYC tier. Limits are enforced on **every** movement of e-money,
measured from the ledger itself.

| Tier | How to reach it | Max balance | Per transaction | Sent per day | Sent per month |
|---|---|---|---|---|---|
| 0 Minimum | Phone + OTP | GH₵5,000 | GH₵3,000 | GH₵3,000 | GH₵10,000 |
| 1 Medium | Ghana Card verified with NIA / KYC provider | GH₵40,000 | GH₵15,000 | GH₵15,000 | Unlimited |
| 2 Enhanced | + live selfie matched to the Ghana Card photo | GH₵75,000 | GH₵25,000 | GH₵25,000 | Unlimited |

These follow the Bank of Ghana mobile-money account tiers (Minimum / Medium / Enhanced).
BoG publishes no separate per-transaction cap, so one transaction may be up to the
day's allowance. They live in `KYC_TIER_LIMITS` in settings — a configuration change
if BoG revises them, no code. **[VERIFY]** against the directive in force at go-live.

**Selfie liveness (tier 2):** the app takes a live selfie; the identity connector
checks it is a real person and matches the Ghana Card photo (`verify_liveness`). Only
the provider's score and reference are stored — never the photo. `POST
/api/v1/kyc/upgrade/selfie` (multipart `selfie`, JPEG/PNG ≤ 5 MB, tier 1 required).

- **Money in** (top-up, transfers received, cash-in, remittances, loans) is checked
  against the per-transaction limit and balance cap.
- **Money out** (transfers, cash-out, payments, premiums) is checked against the
  per-transaction, daily and monthly limits.
- **Ghana Card numbers are encrypted at rest.** A keyed hash enforces one card per
  SokoPay identity without storing the number in the clear. Staff never see the full
  number, only the last digits.
- Identity checks go through the `identity` connector: NIA direct, or a KYC provider
  with NIA access. Both are placeholders until contracted.

## 2. Compliance holds (freezing a wallet)

In Django admin → **KYC profiles**, compliance can **place** or **release a hold**.
A held wallet can still **receive** money, but nothing can **leave** it. Each hold
records who placed it and when. Use it for suspected fraud, AML investigations or
legal orders, and record the case reference in your case-management system.

## 3. Safeguarding — e-money must be fully backed

Customers' and agents' e-money is SokoPay's liability and must be held **in trust** at
licensed bank(s). The daily check (05:00, after reconciliation) compares:

- **Owed**: all customer wallets + agent float + transfers in flight (from the ledger)
- **Held**: the latest balance of every active trust account

Results: **OK** (fully backed), **SHORTFALL** (owed exceeds held), **STALE** (a bank
balance older than 36 hours), **NO DATA** (e-money exists but no trust balance is
recorded). Anything other than OK is logged as **CRITICAL** and should page finance and
compliance.

**Daily routine for finance (until a bank balance API is integrated):**
1. Get the trust account statement balance.
2. Admin portal → **Safeguarding** → record it. The check runs immediately.
3. If the result is not OK, top up the trust account the same day and escalate per
   your incident procedure.

Set up trust accounts in Django admin → *Safeguarding → Trust accounts* (store only the
last 4 digits of the account number).

## 4. Insurance and loans in the wallet

- **Premiums**: once the insurer approves an application, the customer can pay
  premiums from the wallet. The money is recorded as owed to that insurer
  (`financial_partner_payable:<insurer>`), and finance remits it on the agreed schedule.
- **Loans**: when the lender funds an approved loan, its callback (or ops, on the
  lender's written instruction) calls `disburse_loan` with the lender's reference. The
  wallet is credited **once**, even if the lender retries.
- SokoPay never underwrites or lends. Every application carries the customer's
  timestamped consent to share their details with that partner.

## 5. Inbound remittances

A licensed international partner converts the currency, settles cedis to SokoPay and
sends a **signed webhook** (`/api/v1/remittance/<partner>/webhook`). SokoPay:

1. Rejects anything with a bad signature (401).
2. Credits each remittance **once**: retries are recognised by the partner's reference.
3. Credits the recipient's wallet, creating a basic wallet if they're new.
4. **Rejects** (and records why) if the credit would break the recipient's limits, or
   if it isn't settled in GHS. The partner then returns the money to the sender.
   SokoPay never holds funds it can't credit.

## 6. Agents

Agents hold e-money **float**. Cash-in moves float to the customer's wallet; cash-out
moves it back. The customer's KYC limits apply to both. Agent float counts toward
safeguarding liabilities.

## Go-live checklist (DEMI)

- [ ] BoG DEMI licence granted; integrity capital deposited.
- [ ] Trust account(s) opened with licensed bank(s); recorded in admin.
- [ ] `KYC_TIER_LIMITS` confirmed by compliance against the BoG directive.
- [ ] Identity provider contracted (NIA or KYC provider); connector implemented.
- [ ] GhIPSS participation (direct or sponsored) for interop transfers; connectors live.
- [ ] Remittance partner contracted; webhook secret configured.
- [ ] Insurance/lending partners contracted; NIC/BoG licences verified; activated in admin.
- [ ] Monitoring alerts wired for `sokopay.safeguarding` CRITICAL logs.
- [ ] `SOKOPAY_ACTIVE_LICENCE=DEMI` deployed.


## Agent cash-in and cash-out (customer protection)

- **Cash-in:** the customer gives their phone number or **SokoPay wallet ID**; the agent's
  app shows the customer's name ("Ama M.") to read back before crediting. Numbers with no
  SokoPay account are refused — money is never put into an account nobody owns.
- **Cash-out** needs the customer, three times over:
  1. the customer presses **Cash out** in their own app (opens a 10-minute window for one request);
  2. the agent requests the amount;
  3. the customer approves with their PIN (5 minutes to do so).
  Agents can never debit a wallet alone, never see the customer's balance, and their app
  shows **"Do NOT hand over cash"** unless the customer approved.
- **Wallet ID:** 10 digits starting with 7 (never confused with a phone number), with a
  check digit that rejects any single mistyped digit.
