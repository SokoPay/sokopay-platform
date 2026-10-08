# Operations, merchant services and pricing

What was added on 2026-10-08, how each piece works, and what still needs a decision.
Fraud and AML monitoring has its own document: AML-COMPLIANCE.md.

## Customer account basics

| Action | API | Rules |
|---|---|---|
| Change PIN | `POST /auth/pin/change` | Needs the current PIN. Other devices are signed out. |
| Forgot PIN | `POST /auth/pin/forgot`, then `POST /auth/pin/reset` | OTP plus the Ghana Card number when verified. All devices signed out. Sending capped at GH₵500 for 24 hours. |
| Profile | `GET/PATCH /auth/profile` | The name is locked once the Ghana Card is verified. A name change re-runs sanctions screening. |
| Close account | `POST /auth/close` | Needs the PIN, a zero balance, nothing pending, and no business or agent role. Closed accounts are anonymised after the retention period (`DATA_RETENTION_YEARS`). |

## Back-office pages (role-gated)

Staff roles are Django groups: `operations`, `compliance`, `finance` and `support`. Superusers see everything.

| Page | Role | What it does |
|---|---|---|
| Agents | operations | Register (existing account), activate, suspend with a reason, record float purchases. |
| Float approvals | operations, finance | Maker-checker: a different person approves after checking the money arrived. |
| Cash-outs | operations | 24-hour counts and the agents with the most declines, failures or expiries. |
| KYC | compliance | Tier counts, wallets on hold, manual hold and release. AML holds are released only from the alert. |
| Reconciliation | finance | Daily runs and breaks. Resolving a break needs a note and records who did it. |
| Support lookup | support, compliance, operations | Find a customer by phone, wallet ID or SP- reference. Shows the balance, KYC, devices and 30 days of activity. Can sign the customer out everywhere. Never shows AML reasons. |
| Disputes | operations | Decide answered or overdue disputes. |
| Returns | finance, compliance | Monthly regulatory figures, with a CSV download. |
| Prices | finance | Fees and agent commissions (see below). |

## Refunds

- **Who:** merchant Owner or Finance, in the portal (password plus 2FA) or the app (PIN again).
- **Where the money goes:** back to the original payer by the original method only. A wallet payment is credited instantly. A MoMo payment is paid out through the rail and tracked until the partner confirms.
- **Limits:** never more than was paid, full or partial, within 180 days. The merchant fee is not returned.
- **Failure:** a refused MoMo payout is reversed back to the merchant balance. Card refunds aren't automated yet.

## Disputes

1. The customer taps "Report a problem" on a business payment's receipt.
2. The merchant has 7 days to refund or explain.
3. SokoPay operations decide if it isn't settled, or once the deadline passes.

While a dispute is open, its amount is held from settlement and bulk payouts. Each customer can have 5 open disputes at most, and must raise them within 120 days of the payment.

## Merchant webhooks

These are set up in the portal under Developer, then Webhooks. Events:

- `payment.succeeded`
- `payment.failed`
- `refund.succeeded`
- `refund.failed`
- `settlement.paid`
- `settlement.failed`
- `dispute.opened`
- `dispute.resolved`

Each event is signed in the `SokoPay-Signature` header as `t=<unix>,v1=<HMAC-SHA256(secret, "<t>.<body>")>`. Merchants should reject timestamps older than 5 minutes and de-duplicate on the event `id`.

Failed deliveries are retried after 1 min, 5 min, 30 min, 2 h, 6 h, 12 h and 24 h. The endpoint switches off after 50 failures in a row. The SSRF protections are listed in SECURITY-REVIEW.md, finding 20.

## Statements

The statement is built from the ledger. It shows the opening balance, every movement with a running balance, and the closing balance.

- **Customer:** `GET /statements?from&to[&file=csv|pdf]`.
- **Merchant:** `GET /merchant-app/statements` and the portal Statements page.
- **App downloads:** the apps open PDFs or CSVs through `POST /statements/link`, which returns a single-use link valid for 5 minutes.

PDFs come from a small built-in writer, so no PDF library is needed.

## Regulatory figures

`apps/compliance/regulatory.py` produces a month's figures:

- transactions by product;
- customers by status and KYC tier;
- month-end e-money liabilities and the safeguarding status;
- agents and merchants;
- disputes and complaints;
- AML statistics.

**[VERIFY]** Compliance must map these onto the current Bank of Ghana return template. Nothing is submitted automatically.

## Fees and agent commissions

Prices are `PriceRule`s. One finance user proposes a rule and a different finance user approves it. Rules can't be back-dated, and a rule approved late starts at the moment of approval. A rule's value is the flat amount plus a percentage, kept between an optional minimum and maximum.

Until a rule is approved, the launch defaults apply:

| Product | Launch default |
|---|---|
| Bill | GH₵0.50 |
| Airtime and data | Free |
| Bulk payout | 0.75%, minimum GH₵0.50 |
| Cash-out fee | Free |
| Agent commissions | Zero |

**Cash-out fee.** The fee is fixed when the agent raises the request, shown to the customer before they approve, and paid on top of the cash amount.

**Commission.** Commission accrues on each cash-in and cash-out, and is paid into the agent's float on the 1st of each month. Agents see "Commission earned" on their home screen.

**[VERIFY]** Before launch, set the cash-out tariff and the commission schedule.

## Customer app additions (items 9 to 16)

| Feature | How it works |
|---|---|
| Merchants screen | Follows the 7th reference screenshot in SokoPay colours. It has "Pay new merchant" (scan or type a code) and two tabs: Recently paid and Saved merchants. "Pay again" re-checks the merchant every time. |
| Saved recipients | Recipients are saved from a successful send, or from the star on a paid merchant. The server re-checks the account before saving, so typos are never stored. The limit is 50 per customer. |
| Cross-border send | The steps are quote, then reason and PIN, then send. The partner is set by `CROSS_BORDER_PROVIDER`: Onafriq or Brij once contracted, "mock" in development. |
| Tickets and food | Prices always come from the partner's list on the server. Payment is from the wallet and is refunded if the partner refuses. Ticket codes appear under My tickets. Partners are set by `LIFESTYLE_PARTNERS`. |
| Savings, invest, pension | "Pay in" moves money from the wallet to the partner. "Withdraw" is a request that operations confirm against the provider's payment reference (Back office, then withdrawals). Pension withdrawals go through the trustee. |
| USSD | `POST /api/v1/ussd/callback?key=<USSD_SHARED_SECRET>`. The menu offers balance, send, airtime, cash out (open the window, then approve the agent's request with the PIN), mini statement and wallet ID. **[VERIFY]** the aggregator's exact format and authentication. |
| SMS backup | Important alerts go by SMS when no push reached a phone, once only. These are cash-out approval requests, cash withdrawn, PIN reset, and a new settlement account. |
| Online checkout | Public pages `/m/<code>`, `/q/<token>`, `/l/<token>` and `/c/<token>` take payment by MoMo prompt. Merchants make payment links in the portal under Links. Websites create checkout sessions with `POST /api/v1/merchant/checkout-sessions` and must confirm by webhook. Prompts are limited per IP and per phone. Test-key sessions never prompt. |

Cross-border controls:
- The sender must have a verified Ghana Card.
- Each quote is bound to one customer and can be used once.
- The recipient is sanctions-screened. A match is refused with a neutral message, raises an AML alert, and holds the sender's wallet.
- Money is reserved before sending and refunded only on a definite refusal.

## Still open

- Card refunds, and fees on transfers to other networks, aren't priced or automated.
- Flutter tests for all three apps pass locally and in CI. The native-hook packages were pinned out (see mobile/README.md).
- Partner payouts for savings withdrawals are confirmed by operations until each provider's API is connected.
