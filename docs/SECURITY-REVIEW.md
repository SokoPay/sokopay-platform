# Security review — findings and fixes (2026-10-07)

A pass over the whole backend looking for ways money could move without authority,
data could leak, or a deployment could be misconfigured into danger. Each finding
lists what was wrong, what was done, and where the test is. Findings marked **open**
need a decision or an external input and are not fixed in code.

## Fixed

| # | Finding | Risk | Fix | Test |
|---|---|---|---|---|
| 1 | Mock rail and mock connectors (`MockRail`, mock transfer/identity/remittance/financial) were constructible in any environment. A production box with `RAIL_PROVIDER=mock`, or a request to `/api/v1/remittance/mock/webhook` signed with the default secret, could credit real wallets. | Critical | New setting `ALLOW_MOCK_INTEGRATIONS` (default **off**; `dev.py` on; `prod.py` forces off). Both registries refuse to build a mock unless allowed. `prod.py` refuses to boot if `RAIL_PROVIDER`, `KYC_IDENTITY_PROVIDER` or any `INTEROP_ROUTES` entry is `mock`, or `RAIL_WEBHOOK_SECRET` is unset. `seed_marketplace --dev` refuses without the flag. | `apps/common/tests/test_security.py` |
| 2 | Rail webhook accepted any `<provider>` in the URL; an unknown name raised inside the service (500) and a configured-but-unused rail could receive callbacks. No payload cap. | High | `RailWebhookView` answers only for `settings.RAIL_PROVIDER` (404 otherwise), caps the body at 64 KB (413). Same cap on the remittance webhook. | same |
| 3 | No way to end a mobile session: JWTs stayed valid until expiry (access 15 min, **refresh 30 days**) after sign-out, PIN change or a lost phone. Refresh tokens could be replayed. | High | Denylist table `RevokedToken` (by `jti`) + per-user `token_generation`. `POST /auth/logout` revokes the access token and the supplied refresh token (only if it belongs to the caller); `POST /auth/logout-all` bumps the generation (every device out). Refresh tokens rotate — a used one is revoked. Changing an existing PIN signs out every other device and returns fresh tokens to the device that changed it. Daily purge task. Flutter `AuthController.signOut()` now calls the server first. | `apps/accounts/tests/test_auth.py` |
| 4 | Idempotency keys for payments, wallet funding, P2P, interop and merchant charges were global: a key reused by another user/merchant returned the first caller's object. | High | Keys are scoped per actor (`u:<user>:<key>`, `m:<merchant>:<key>`). | wallet / payments tests |
| 5 | P2P `send_p2p` created the recipient if the phone was unknown, so a typo moved e-money to a wallet nobody owned. | High | Recipient must be an existing active account; `/wallet/send/lookup` lets the app show the name first. | `apps/wallet/tests/test_wallet.py` |
| 6 | Any merchant role (including cashier/developer) could add a settlement account — the destination of all the merchant's money. | High | Owner/Finance only, enforced in the view. | portal tests |
| 7 | A Developer could issue **live** API keys, and live keys could be issued before the business was approved. | Medium | Developers manage test keys only; live keys need an approved business and Owner/Admin. | portal view |
| 8 | Merchant bulk payouts (new): uploaded spreadsheets could carry formula injection into the results export; the uploader could approve their own batch by forging the POST. | Medium | Rejected on upload, escaped on export; approval rules live in the service layer, not the template. | `apps/bulk/tests/test_bulk.py` |
| 9 | Selfie upload: an arbitrary file could be sent as a "selfie". | Low | Magic-byte check (JPEG/PNG), 5 MB cap, only the provider's score/reference stored. | `apps/kyc/tests/test_kyc.py` |
| 10 | **Merchant portal had no 2FA** — a leaked Owner/Finance password could approve bulk payouts and change settlement accounts. | High | TOTP 2FA **mandatory for Owner/Admin/Finance** (`MERCHANT_2FA_REQUIRED_ROLES`), optional for Cashier/Developer but enforced once enrolled. 10 one-time **backup codes** at enrolment (HMAC-hashed, single-use via one atomic UPDATE), regenerable from `/dashboard/security/` only with a current code. Staff can **reset** a member's 2FA from the merchant page (audited, call-back verification required by procedure). | `apps/portal/tests/test_portal_security.py` |
| 11 | **No portal rate limiting** — unlimited password and 2FA guesses. | High | Redis fixed-window counters (`apps/portal/ratelimit.py`): 5 wrong passwords per phone / 30 per IP per 15 min; 5 wrong 2FA codes per user per 15 min → signed out and locked; the lock is checked *before* the password so it isn't an oracle; same message for unknown phones. Per-user hourly throttles on bulk upload, API keys, QR requests, settlement requests and settlement-account changes. Client IP from X-Forwarded-For only via `TRUSTED_PROXY_COUNT` (prod 1 = the ALB), so a forged header can't dodge the IP limit. Cache keys are hashed (no phone numbers in Redis). WAF gets an extra edge rule: 100 req / 5 min / IP on login, 2FA and `/api/v1/auth/`. Lockouts, enrolments, resets and backup-code use are written to an append-only `SecurityEvent` log. | same |

| 12 | Merchant settlement ignored the payout result: a payout the partner refused stayed "processing" with the merchant's money already moved to clearing. | High (once real rails are live) | Definitive refusal → ledger reversal + settlement `FAILED`; unknown outcome stays `PROCESSING` (never refund what may still be paid). Real Korba/Nsano adapters can't carry live money until their spec is confirmed and the partner certified (two independent gates). See RAILS-INTEGRATION.md. | `apps/merchants/tests/test_checkout_and_settlement.py`, `apps/rails/tests/test_partner_rails.py` |

| 13 | **Agents could cash out of any wallet unilaterally** — agent cash-out debited the customer's wallet with only their phone number; no customer involvement. | Critical | Three-step cash-out: (1) the customer presses **Cash out** in their app, opening a 10-minute window for ONE request; (2) the agent requests an amount; (3) the customer approves with their PIN (server-checked, shared lockout). Requests expire in 5 min, one open per customer, an agent can't cash out their own wallet, and the agent is never shown the customer's balance. | `apps/agents/tests/test_cash_out_approval.py` |
| 14 | **Cash-in to a mistyped number created a ghost account** holding real money. | High | Cash-in (and cash-out) require an existing SokoPay customer, identified by phone or **wallet ID** (10 digits starting 7, Luhn check digit catches typos); the agent reads the customer's name back ("Ama M.") before crediting. | `apps/agents/tests/test_agents.py`, `apps/wallet/tests/test_wallet_number.py` |
| 15 | Float top-ups booked against the *mock* rail's clearing account (hard-coded). | High | Uses `RAIL_PROVIDER`. | `apps/agents/tests/test_agent_notifications.py` |
| 16 | **SIM swap took over the wallet**: OTP alone returned a full session, so whoever held the SIM could spend. PIN change didn't ask for the current PIN. | Critical | OTP alone now only leads to the PIN screen for existing PIN holders. Forgot-PIN needs OTP **and** the Ghana Card number (when verified), signs out every device, and caps sending at GH₵500 for 24 h. PIN change needs the current PIN. | `apps/accounts/tests/test_account_basics.py` |
| 17 | Float top-ups were credited by one staff member with no evidence of payment. | High | Maker-checker: recorded against a unique bank/MoMo payment reference, approved by a *different* staff member, capped per request. | `apps/portal/tests/test_ops_portal.py` |
| 18 | Refund abuse paths: refunding more than was paid, twice, to a different phone, or after settling the money out. | High | Refunds go only to the original payer by the original method; the sum is checked under a row lock on the payment; the merchant balance can't go negative; a 180-day window. A MoMo refund's ledger reservation commits **before** the payout is sent; a refused payout is reversed. | `apps/merchants/tests/test_refunds_disputes.py` |
| 19 | A merchant could empty their balance while a customer dispute was open. | Medium | Active disputes' amounts are held from settlement **and** bulk payouts; staff can decide once the 7-day answer window passes, so a merchant can't stall. | same |
| 20 | Outbound webhooks are a server-side request forgery (SSRF) channel. | High | https only; every resolved address must be public (private, loopback, link-local, metadata and IPv4-mapped ranges refused); the connection is **pinned** to the checked address (stops DNS rebinding); no redirects; responses read to 4 KB; secrets encrypted at rest and shown once; auto-off after 50 failures. | `apps/merchants/tests/test_webhooks.py` |
| 21 | Statements: CSV formula injection from merchant-controlled names; login tokens in download URLs. | Medium | CSV cells starting `= + - @` are escaped. Apps open PDFs through a **single-use, 5-minute signed link**; the merchant role is re-checked when the link is used. | `apps/activity/tests/test_statements.py` |
| 22 | Prices were constants in code; one developer could change what customers pay. | Medium | Fees and agent commissions are approved rules (maker-checker, never back-dated, typo caps of 10% / GH₵100). No commission on an agent's own wallet or below GH₵5; suspended agents' commission is held. | `apps/pricing/tests/test_pricing.py` |
| 23 | Concurrency bugs only visible on PostgreSQL: a balance-row creation race and deadlock in the ledger, refunds failing (row lock across an optional join), a large-transaction record overflowing its column. | High | Balance rows created before locking, one ordered lock; `select_for_update(of=("self",))`; column widened. Proved by threaded tests on Postgres in CI. | `apps/ledger/tests/test_concurrency_postgres.py` |
| 24 | Hosted checkout pages can be abused to spam MoMo approval prompts to strangers' phones. | Medium | Per-IP and per-payer-number limits; fixed amounts come from the server; test-key sessions never prompt; return URLs must be https; merchants must confirm by webhook, not by the redirect. | `apps/merchants/tests/test_hosted_checkout.py` |
| 25 | USSD callback is an unauthenticated money channel if exposed. | High | Shared secret (constant-time compare) required; refused when unset outside dev; the caller's number comes from the network; PIN with the shared lockout on every money step; nothing typed is logged. | `apps/ussd/tests/test_ussd.py` |
| 26 | Cross-border sends to sanctioned recipients; quote replay. | High | Recipient screened (match refused neutrally, AML alert, wallet hold); quotes bound to the customer and single-use; verified Ghana Card required. | `apps/wallet/tests/test_cross_border.py` |
| 27 | Lifestyle purchases priced by the app. | Medium | Prices only from the partner list on the server. | `apps/wallet/tests/test_lifestyle.py` |

## Controls already in place (verified during the review)

- Single ledger write path with row locks and idempotency; postings are append-only;
  protected liability accounts cannot go negative (`apps/ledger`).
- Webhooks: signature verified, raw event stored, status **re-queried** from the rail —
  the callback body is never trusted.
- Licence gate on every regulated action; features ship switched off.
- Maker-checker on settlements (staff) and bulk payouts (merchant), with the maker
  barred from approving.
- Staff portal needs TOTP 2FA; sessions are 30 min in production, cookies Secure/HttpOnly.
- Secrets only from the environment; prod refuses to start without the field-encryption
  key; Ghana Card numbers encrypted (Fernet) with a keyed lookup hash.
- Argon2 for passwords and PINs; OTPs hashed in cache, single-use, rate-limited; PIN
  lockout after 5 failures.
- DRF throttling (30/min anonymous, 120/min user); CORS allow-list only; HSTS;
  no PII to Sentry.
- UUID primary keys everywhere a URL or API exposes an id.
- `bandit` clean (no medium/high), `ruff` clean, Django `check` clean.

## Open (needs a decision or an external party)

1. ~~Portal rate limiting~~ and 2. ~~Merchant portal 2FA~~ — done (findings 10–11).
   Remaining nice-to-haves: WebAuthn/passkeys as a second factor option; email/SMS
   alert to the user on 2FA reset or lockout (needs the SMS provider live).
3. **Real connectors** (Korba/Nsano, GhIPSS, NIA, FCM service account) are placeholders;
   each must be reviewed when the partner's spec arrives — especially webhook
   signature schemes.
4. **Push notification content** goes through FCM (Google). Keep bodies free of
   balances/IDs beyond what is already there if the DPC view is that amounts are
   personal data.
5. **Postgres-only guarantees**: `SELECT … FOR UPDATE` concurrency tests must run on
   Postgres in CI (SQLite ignores row locks). See BUILD-STANDARDS.md.
6. **Backups / DR** are Terraform-defined but not applied or exercised.
7. **Penetration test** by an external party before BoG submission.
