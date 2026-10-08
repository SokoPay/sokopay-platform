# 2. Trust

Trust means customers, merchants, partners and the regulator can rely on SokoPay to keep money and data safe and to treat people fairly and openly.

## Controls in place

**Money safety**

| Control | Where | Evidence |
|---|---|---|
| Double-entry ledger in integer pesewas. The books always sum to zero, and wallets can't go negative | `apps/ledger` | ledger property tests, Postgres concurrency tests |
| Customer e-money matched against trust-account balances daily (safeguarding); a shortfall pages on-call | `apps/safeguarding`, `infra/monitoring.tf` | safeguarding tests |
| Agents can never take money from a wallet on their own: the customer opens a window, the agent requests, the customer approves with their PIN | `apps/agents` | `test_cash_out_approval.py`, USSD tests |
| Refunds go only to the original payer; disputed amounts are held | `apps/merchants/refunds.py`, `disputes.py` | `test_refunds_disputes.py` |

**Account security**

| Control | Where | Evidence |
|---|---|---|
| PIN (Argon2) with lockout | `apps/accounts` | account tests |
| SIM-swap defence: OTP alone never gives a session, and PIN reset needs the Ghana Card and caps sending for 24 hours | `apps/accounts` | `test_account_basics.py` |
| Revocable tokens | `apps/accounts` | account tests |
| Biometric app lock and app-switcher privacy cover | shared Flutter package | `flutter analyze` |
| 2FA for staff and merchant money roles; rate limits on every login and sensitive action | `apps/portal` | `test_portal_security.py` |

**Data protection**

| Control | Where | Evidence |
|---|---|---|
| Ghana Card numbers encrypted at rest; selfies never stored | `apps/common/encryption.py`, `apps/kyc` | KYC tests |
| Payer phones masked to merchants; no personal data sent to the error tracker | merchant views, `prod.py` | merchant API tests |

**Fair treatment**

| Control | Where | Evidence |
|---|---|---|
| Fees shown before confirmation, including the cash-out fee; prices come from the server, never the app | pricing, lifestyle | `test_pricing.py`, `test_lifestyle.py` |
| No tipping off: support never sees AML reasons, and a customer refused for sanctions gets a neutral message | `apps/compliance`, support page | `test_ops_portal.py`, cross-border tests |

**Partner integrations**

| Control | Where | Evidence |
|---|---|---|
| Webhooks signed; partner callbacks verified and re-queried, never trusted | `apps/merchants/webhooks.py`, `apps/payments` | `test_webhooks.py`, rail tests |
| Protection against server-side request forgery on merchant webhooks; MoMo prompt spam limited | webhooks, hosted checkout | same, `test_hosted_checkout.py` |

**Transparency**

| Control | Where | Evidence |
|---|---|---|
| Privacy policy states exactly what is collected and why, including offshore hosting | `docs/legal`, served at `/legal/` and linked in every app and portal | `test_legal.py` |
| Security review with 27 findings fixed | `docs/SECURITY-REVIEW.md` | linked tests |

## Gaps and actions

| Priority | Gap | Action |
|---|---|---|
| High | **No external penetration test** | Commission one (backend, portals, apps, USSD) before launch. |
| High | **Legal documents are drafts.** Offshore hosting needs Data Protection Commission and Bank of Ghana comfort | Counsel and DPO sign-off, DPC registration, decision on hosting (see the top 10 in the summary). |
| High | **APKs are debug-signed** (internal testing only) | Create a Play upload key in a secure store and add a release signing configuration fed by CI secrets. |
| Medium | **No certificate pinning or rooted-device checks** in the apps | Add certificate pinning for the API host, and a warning on rooted or jailbroken devices, before the public launch. |
| Medium | **Screenshots of money screens are possible** on Android (FLAG_SECURE not set; your decision is pending) | Decide. The app-switcher cover is already on. |
| Medium | **Push notifications go through Google (FCM)**, and Firebase isn't configured yet | Keep amounts and IDs minimal in pushes (already the case). Add FCM credentials. |
| Low | Support contact details are placeholders | Fill them in `SUPPORT_CONTACT` and the legal documents. |
