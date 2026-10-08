# Licence–Capability Matrix

This is the human-readable version of the rules enforced in code at
`backend/apps/licensing/capabilities.py`. It is the contract between what Bank of
Ghana (BoG) licenses us to do and what the software will actually let us do.

## How to read this

- A **capability** is one regulated activity (e.g. "hold customer funds").
- Each **licence** switches on a set of capabilities. Higher licences include
  everything the lower ones allow, plus more.
- The deployment's active licence is set by one environment variable,
  `SOKOPAY_ACTIVE_LICENCE`. SokoPay launches at **PSP_STANDARD**.
- Every money-moving feature checks its capability before running. If the licence
  doesn't permit it, the action is refused — even though the feature is fully built.

This is how we "build everything now and roll out as licences arrive": the code for
the wallet, agents and cross-border is all present, shipped switched off, and turned
on the day BoG grants the matching licence.

## The matrix

| Capability | Standard | Medium | Enhanced | DEMI | What it means (plain English) |
|---|:--:|:--:|:--:|:--:|---|
| Bill payment | ✅ | ✅ | ✅ | ✅ | Pay ECG, water, TV, school fees |
| Airtime & data | ✅ | ✅ | ✅ | ✅ | Buy airtime and data bundles |
| Pay a merchant (risk upstream) | ✅ | ✅ | ✅ | ✅ | Customer pays a shop; the partner carries the risk |
| Merchant aggregation | — | ✅ | ✅ | ✅ | Sign up shops and collect payments for them |
| Biller aggregation | — | ✅ | ✅ | ✅ | Connect billers and route payments to them |
| POS deployment | — | ✅ | ✅ | ✅ | Put card/MoMo terminals in shops |
| Card acquiring | — | ✅ | ✅ | ✅ | Accept Visa/Mastercard/GH-Link (partner-hosted) |
| Non-cash instruments | — | ✅ | ✅ | ✅ | Cheques, prepaid instruments |
| Settle funds to merchants | — | ✅ | ✅ | ✅ | Pay shops their collected money |
| Bulk disbursement | — | ✅ | ✅ | ✅ | A merchant pays staff/suppliers from its balance (MoMo, bank, other wallets; SokoPay wallets need DEMI) — see [BULK-PAYMENTS.md](BULK-PAYMENTS.md) `[VERIFY tier]` |
| Own payment processing | — | — | ✅ | ✅ | Process on our own rails, not only a partner's |
| Third-party gateway | — | — | ✅ | ✅ | Provide a payment gateway to other businesses |
| Inward remittance | — | — | ✅ | ✅ | Receive international money transfers |
| EMV card issuing | — | — | ✅ | ✅ | Print and personalise chip cards |
| Closed-loop virtual cards | — | — | ✅ | ✅ | Limited-use virtual cards |
| **Cross-border via PAPSS** | — | — | ✅ | ✅ | Pan-African payments (through a partner bank) |
| **Cross-border transfers via partners** | — | — | ✅ | ✅ | Customers send money abroad through licensed partners (Onafriq, Brij) `[VERIFY tier]` |
| **Financial marketplace** | — | — | ✅ | ✅ | List insurance, loan, savings, investment and pension products from licensed partners and forward applications |
| **Hold customer funds (wallet)** | — | — | — | ✅ | Keep a customer's money in a SokoPay wallet |
| **Person-to-person transfer** | — | — | — | ✅ | Send money wallet-to-wallet |
| **Cash-in / cash-out** | — | — | — | ✅ | Add or withdraw cash through agents |
| **Agent network** | — | — | — | ✅ | Recruit and manage agents |
| **Wallet ↔ bank transfer** | — | — | — | ✅ | Move money between wallet and bank |
| **Inbound remittance termination** | — | — | — | ✅ | Pay out international transfers into wallets |
| **Embedded financial products** | — | — | — | ✅ | Insurance premiums paid from, and loans disbursed into, the wallet — only with regulated partners |

Interoperable transfers to other fintechs, MoMo wallets and banks fall under
**Person-to-person transfer** and **Wallet ↔ bank transfer** (DEMI). See
[CONNECTORS.md](CONNECTORS.md) for every external party and its status.

## The single most important rule

> **SokoPay cannot hold customer money until the DEMI licence is granted.**

Under PSP licences, money we collect belongs to the merchant or biller and must sit
with the Enhanced PSP partner or in a designated settlement/trust account. The ledger
tracks it as a **payable** (money we owe out), never as our own or as customer e-money.
The wallet and agent-float accounts exist in the chart of accounts from day one but are
only transacted once `HOLD_CUSTOMER_FUNDS` is enabled by the DEMI licence.

## Open points to confirm with counsel / BoG  `[VERIFY]`

1. The exact structure for holding merchant funds under PSP Medium (partner settlement
   account vs designated trust account).
2. Whether DEMI should inherit all Enhanced capabilities, or be taken as a parallel
   licence with a narrower set (affects `TIER_ORDER` in `capabilities.py`).
3. The PAPSS access route and which partner bank provides it.
4. ISO 27001 and PCI DSS obligations per tier (treated as required where applicable).
