# Payment rails: Korba and Nsano integration

SokoPay moves money on MoMo networks through ONE Enhanced PSP partner ("rail") —
Korba or Nsano, chosen by `RAIL_PROVIDER`. Both adapters exist
(`backend/apps/rails/korba.py`, `nsano.py`) behind one interface (`RailProvider`) so
the choice is configuration, not a rewrite.

## Where things stand

| Part | Status |
|---|---|
| HTTP client: HTTPS-only, TLS verified, 5 s connect / 30 s read, 2 MB response cap, no redirects, no proxy env | **Done, tested** (`http.py`) |
| Request signing (HMAC-SHA256, timestamp + nonce) and webhook verification (constant-time, optional timestamp replay window) | **Done, tested** (`signing.py`) |
| Collect, payout, bill pay, status re-query, wallet-name and bill-account lookup, settlement report (paged) | **Done, tested** (`partner.py`) |
| Safety rules (below) | **Done, tested** |
| Certification gate — unconfirmed adapters can't move live money | **Done, tested** |
| **Partner wire details**: endpoint paths, field names, network codes, status words, signature header/scheme | **Working values, UNCONFIRMED** — Korba and Nsano don't publish their API references; they come with the partner agreement + sandbox keys |

So the adapters are complete *except* for the facts only the partners' docs can give.
Those are isolated as class attributes and three small `*_body()` methods at the top
of each adapter file, every one marked `[VERIFY]`.

## Safety rules the adapters enforce

1. **Success only on a recognised success word.** Any status the spec doesn't list is
   `UNKNOWN` (stay pending, re-query) — never `SUCCEEDED`.
2. **Never "failed" when it might have gone through.** Read timeouts, connection
   resets, 5xx, 408 and 409 (duplicate id) → `UNKNOWN`. Only a definitive 4xx
   rejection, a recognised failure word, or a request that provably never left us
   (connect timeout / TLS failure) → `FAILED`. Otherwise we could refund a customer
   whom the partner still pays.
3. **Money POSTs are never retried.** Status GETs are (twice, with backoff).
4. **Our own transaction id** is sent and everything is keyed on it, one per operation:
   `<reference>-C` collect, `-P` payout, `-B` bill (hashed deterministically if over the
   partner's length limit). Retries reuse the id so the partner de-duplicates. If the
   partner echoes a *different* id, the result is `UNKNOWN` and logged.
5. **Webhooks are evidence, not truth.** Signature verified on the raw body; payload
   of a bad-signature call is not stored; the payment is then re-queried via
   `get_status()` before any money moves (apps/payments).
6. **Logs** carry method, path, HTTP status and timing — never bodies, secrets or phone
   numbers.

## Configuration (environment)

```
RAIL_PROVIDER=korba                     # or nsano
KORBA_BASE_URL=https://…                # from the partner
KORBA_CLIENT_ID=…
KORBA_CLIENT_SECRET=…                   # Secrets Manager, never in git
KORBA_WEBHOOK_SECRET=…
KORBA_CALLBACK_URL=https://api.sokopay.com.gh/api/v1/rails/korba/webhook
RAIL_CERTIFIED_PARTNERS=korba           # ops sets this ONLY after certification
```
(`NSANO_*` likewise.) Production refuses to start if the active rail is missing any of
these or isn't in `RAIL_CERTIFIED_PARTNERS`.

## Go-live checklist (per partner)

**Developer, with the partner's API reference:**
1. Fill every `[VERIFY]` value in the adapter: paths, `NETWORK_CODES`, `AMOUNT_AS`,
   `MAX_REF_LEN`, response field paths, the three status-word sets, webhook header
   names/encoding, `make_signer()` (what is signed, header format), `*_body()` fields.
2. List every status word the docs define. Anything not in the success / failed /
   pending sets will be treated as unknown — that's safe but will stall payments.
3. Set `DOCS_VERSION` and `SPEC_CONFIRMED = True`. Run `pytest apps/rails` — the
   guarantees are tested against whatever the spec says.

**Sandbox certification (developer + ops), recording each response as a fixture:**
4. Collect: approve on phone → success; decline → failed; let it expire → failed.
5. Webhook arrives, signature verifies, payment completes only after the re-query.
6. Forge a webhook (bad signature) → rejected with 401, nothing moves.
7. Kill the network mid-collect → `UNKNOWN`; the 5-minute poller resolves it.
8. Same transaction id twice → partner de-duplicates (no double charge).
9. Payout to MTN, Telecel, AT; payout to an invalid number → refused → settlement
   reversed to the merchant (`FAILED`).
10. Bill pay for ECG prepaid (token returned), postpaid, water, DStv.
11. Name enquiry for a real and a non-existent wallet.
12. Settlement report for a sandbox day matches our records in reconciliation
    (0 breaks), including multi-page.
13. Then ops add the partner to `RAIL_CERTIFIED_PARTNERS` in production.

## One partner, not two

SokoPay will run on **either** Korba **or** Nsano — never both, and there is no
fail-over between them. `RAIL_PROVIDER` names the one in use. Each settlement records
which rail carried it, so even a one-off switch later can't reverse a payout into the
wrong clearing account.

## Merchant settlement payouts

`AWAITING_APPROVAL → PROCESSING → PAID | FAILED` (`apps/merchants/settlement.py`):

1. The merchant's money moves to `partner_clearing:<rail>` and the status becomes
   `PROCESSING` — committed **before** the partner is called (no ledger locks held
   during the network call).
2. The payout is sent: `payout()` for MoMo, `bank_payout()` for bank accounts.
3. The outcome arrives by **webhook** (signature verified, then **re-queried**, body
   never trusted) or the **poller** every 5 minutes (`poll_settlement_payouts`):
   success → `PAID`; definitive failure → money reversed to the merchant → `FAILED`;
   anything else → keep waiting. Applying an outcome is idempotent (duplicate
   webhooks and overlapping polls are harmless). The merchant owner is notified.
4. **Stuck payouts** — still unconfirmed after 24 h, or no partner reference 10 min
   after sending (a crash between steps 1 and 2) — are logged CRITICAL, shown on the
   admin dashboard, and listed under *Approvals → Stuck payouts* with a **Re-check**
   button. They are never re-sent or reversed automatically: confirm with the
   partner's operations team first.

**Bank settlements**: the code path is complete (MockRail exercises it end to end),
but the Korba/Nsano bank-payout endpoint is unknown, so `PATH_BANK_PAYOUT = None` and
`supports_bank_payout` is False. Until the partner provides it, a bank settlement is
**refused at request time** — before any money moves — with "add a mobile-money
settlement account for now". To switch it on: set `PATH_BANK_PAYOUT` and check
`bank_payout_body()` against the docs; add a sandbox bank payout (success + invalid
account) to the certification checklist above.

## Known gaps

- Bank payout endpoint for the chosen partner (see above).
- Bank account **name verification** (the settlement account's `name_check_status`)
  is still set by ops; automate it once the partner's bank name-enquiry is known.
