# Connectors — every external party SokoPay talks to

**The live, always-accurate list is the admin portal's Integrations page**
(`/dashboard/admin/integrations/`), generated from `backend/apps/connectors/registry.py`.
This document explains the model and what each group needs to go live.

## The idea (for everyone)

SokoPay connects to many outside organisations: electricity and water companies, TV
providers, mobile networks, other wallets, banks, card schemes, insurers and lenders.
Each one is a **connector** — a plug with a standard shape. Today most are
**placeholders**: the plug is built and wired into the app, but it isn't connected
until we sign the agreement and receive that organisation's technical details.

Two rules keep this safe:

1. **A placeholder never takes a customer's money.** If a provider isn't live, the app
   refuses the payment *before* charging anyone.
2. **If something slips through anyway, the customer is refunded.** A payment that
   reaches a placeholder after collection goes down the normal refund path.

Status meanings: **available** = money can flow now · **placeholder** = waiting on
agreement/docs/credentials · **mock** = test double for development only.

## How money reaches each party today

Under the PSP Standard/Medium licences, SokoPay reaches billers, airtime and data
**through its Enhanced PSP partner (Korba or Nsano)**. That route — `rail` — is
**available now** (against the mock rail until the partner is signed). Direct
integrations are optional upgrades for later, usually at PSP Enhanced, for better
margins and control.

| Group | Works today via | Direct placeholders |
|---|---|---|
| **Billers** | Partner (`rail`) | ECG (prepaid/postpaid), NEDCo, Ghana Water, Telecel Fibre (formerly Vodafone Fibre), DStv, GOtv, StarTimes |
| **Airtime & data** | Partner (`rail`) | MTN, Telecel, AT (formerly AirtelTigo) |
| **Transfers out of the wallet** *(DEMI)* | MoMo via partner (`rail`) | GhIPSS MMI (wallet interop), GhIPSS Instant Pay (banks), MTN MoMo, Telecel Cash, AT Money, G-Money, Zeepay |
| **Card schemes** | — | Visa (incl. Visa Direct), Mastercard (incl. Mastercard Send), gh-link |
| **Insurance** *(Enhanced)* | — | Partner template (NIC-licensed insurer) |
| **Lending** *(Enhanced)* | — | Partner template (BoG-licensed lender) |
| **Identity (KYC)** *(DEMI)* | — | NIA direct; KYC provider with NIA access (template) |
| **Trust bank** *(DEMI)* | Manual statement entry in the admin portal | Bank balance API (template) |
| **Inbound remittance** *(DEMI)* | — | Remittance partner (template) |

### Notes by group
- **Data bundles**: the bundle list shown today is a **sample** (`is_sample: true` in the
  API). Replace it with the partner's or telco's live catalogue before launch.
- **Account lookup**: before paying a bill or sending money, the app shows the account
  holder's name so the customer can confirm. Supported on the partner route where the
  partner offers it.
- **Interop transfers** use *reserve → send → settle or refund*. The money is moved
  out of the wallet into an "in flight" account before calling the other institution,
  then settled on success or returned on failure. Pending transfers are re-checked
  every 5 minutes.
- **Cards**: SokoPay never handles raw card numbers. Card payments use the acquirer's
  hosted page/fields; payouts to cards use scheme tokens. This keeps PCI DSS scope small.
- **Insurance & loans**: SokoPay lists partner products and forwards applications
  **with the customer's explicit consent** (recorded with a timestamp). The licensed
  partner underwrites or lends; SokoPay does not.

## Licence gating

| Activity | Capability | Licence |
|---|---|---|
| Pay bills, airtime, data | `BILL_PAYMENT`, `AIRTIME_DATA` | PSP Standard |
| Card acquiring | `CARD_ACQUIRING` | PSP Medium |
| Push-to-card payouts | `PAYMENT_PROCESSING` | PSP Enhanced |
| Insurance/loan marketplace | `FINANCIAL_MARKETPLACE` | PSP Enhanced |
| Wallet → other wallets (on/off-net) | `WALLET_P2P` | DEMI |
| Wallet → bank | `WALLET_BANK_TRANSFER` | DEMI |
| Products inside the wallet (premiums, loan disbursement) | `EMBEDDED_FINANCIAL_PRODUCTS` | DEMI |
| Remittances into wallets | `INBOUND_REMITTANCE_TERMINATION` | DEMI |

See [DEMI-OPERATIONS.md](DEMI-OPERATIONS.md) for KYC limits, safeguarding and the
DEMI go-live checklist.

## Turning a placeholder into a live integration (for engineers)

1. Get the agreement, API docs, sandbox credentials, and complete any certification.
2. In `backend/apps/connectors/<group>.py`, replace the placeholder class with a real
   implementation of its interface (`base.py`): map requests/responses to the shared
   types in `types.py`. Keep TLS verification on; secrets come from the environment.
3. Set `available = True` on the class.
4. Add contract tests against recorded sandbox responses, plus the failure paths
   (decline, timeout, duplicate callback).
5. Route traffic to it:
   - a **biller**: set `Biller.connector` (e.g. `"ecg"`) in the admin;
   - a **telco**: `TELCO_ROUTES` in settings;
   - a **transfer** destination: `INTEROP_ROUTES` (e.g. `"wallet:zeepay": "zeepay"`);
   - an **insurer/lender**: create a `FinancialProvider` with that connector key and
     activate it after due diligence.
6. Confirm it shows as **available** on the Integrations page.

Every placeholder's error message states what it needs to go live, and every
`[VERIFY]` marks a detail to confirm against the provider's real documentation.


## Aggregated service providers (added 2026-10-08)

SokoPay aggregates other companies' services; each partner is a connector, live only
when contracted. All are `placeholder` in the admin **Integrations** page until then.

| Category | Connector key | Partner type | Regulator |
|---|---|---|---|
| savings | `savings_partner` | bank / savings & loans | Bank of Ghana |
| investment | `investment_partner` | fund manager / broker | SEC Ghana |
| pension | `pension_partner` | corporate trustee (tier-3) | NPRA |
| ticketing | `ticketing_partner` | event ticketing platform | commercial |
| food | `food_partner` | food ordering / delivery | commercial |
| cross_border | `onafriq` | Onafriq (formerly MFS Africa) | BoG corridor approval |
| cross_border | `brij` | Brij | BoG corridor approval `[VERIFY]` |

Savings, investment and pension plug into the **financial marketplace** (same consented
application flow as insurance and loans; seed templates in `seed_marketplace`). Lifestyle
partners implement `LifestyleConnector` (offerings → purchase → status). Cross-border
partners implement `CrossBorderConnector`: **quote** (rate + fee, short-lived) → customer
confirms → **send** against the quote → status by webhook / re-query; capability
`CROSS_BORDER_TRANSFER`.
