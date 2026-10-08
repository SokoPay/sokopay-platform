# Bulk payments (merchant portal)

A merchant pays many people at once — monthly salaries, commissions, supplier runs —
from their SokoPay balance, by uploading a spreadsheet. Code: `backend/apps/bulk/`,
portal pages at `/dashboard/bulk/`.

## For merchants (how it works)

1. **Download a template** (`.csv` or `.xlsx`) from the Bulk pay page, or export from
   your payroll tool. Columns (headers are matched loosely — "Phone", "Account",
   "Amount (GHS)" all work):

   | column | meaning | examples |
   |---|---|---|
   | `name` | who you are paying | Ama Mensah |
   | `destination_type` | `sokopay` (SokoPay wallet), `momo` (default), `bank`, `wallet` (other fintech) | momo |
   | `institution` | `mtn` / `telecel` / `at` for momo; bank code for bank; `gmoney` / `zeepay` for wallet; blank for sokopay | mtn |
   | `account` | phone number (`0241234567`) or bank account number | 0241234567 |
   | `amount` | cedis, up to 2 decimals, max GH₵50,000 per row | 850.00 |
   | `narrative` | optional note the recipient sees | October salary |

2. **Upload** (Owner, Admin or Finance). Every row is checked: phone format, network,
   amount, duplicates, whether the SokoPay recipient exists and can receive that much
   (their KYC limit), whether we can actually reach that destination yet. Problems are
   shown per row; nothing is paid.
3. **Submit for approval.** If some rows are invalid you can tick *skip invalid rows*
   and pay the rest. The batch must fit the available balance including fees.
4. **Approve** (Owner or Finance — a *different* person from the one who uploaded; if
   the business has only one approver they may approve their own, and this is
   recorded as self-approved). The full amount plus fees is reserved from the balance
   immediately.
5. **Payment runs in the background.** The page updates every few seconds; recipients
   with a SokoPay wallet get an in-app notification. Rows that fail (a declined wallet,
   a destination that is not connected yet) are **refunded to your balance**
   automatically. Download the **results .csv** for your records.

Fees: SokoPay wallet → free; MoMo, bank, other wallets → 0.75% (min GH₵0.50) per
recipient, charged to the merchant. `[VERIFY against partner disbursement pricing]`

The same file cannot be uploaded twice within 7 days unless the earlier batch was
cancelled or rejected — a guard against paying a payroll twice.

## For engineers

**Licence:** `BULK_DISBURSEMENT` (PSP Medium). Rows into SokoPay wallets additionally
require `HOLD_CUSTOMER_FUNDS` (DEMI) and are marked invalid without it.

**Money** (integer pesewas; every entry balances):

```
approve            Dr merchant_payable:<m>   Cr bulk_in_flight        amount + fees of ready rows
row → wallet       Dr bulk_in_flight         Cr customer_wallet:<u>   amount (fee 0)
row → external ok  Dr bulk_in_flight         Cr partner_clearing:<route> amount, Cr fee_revenue fee
row failed         Dr bulk_in_flight         Cr merchant_payable:<m>  amount + fee (refund)
```

Reserving at approval means a settlement running concurrently cannot spend the same
money, and a worker crash leaves funds visibly "in flight" rather than lost. All
ledger posts carry idempotency keys (`bulk-reserve:<batch>`, `bulk-item-paid:<item>`,
`bulk-item-refund:<item>`), so re-running the task is harmless.

**Pipeline:** `parser.py` (CSV/XLSX → rows; 2 MB, 5,000 rows; openpyxl read-only with
cached values — formulas never execute) → `validate.py` (rows → `BulkPayoutItem`
valid/invalid with reasons) → `services.create_batch / submit / approve / reject /
cancel` → Celery `process_bulk_payout` → `process_item` (wallet credit in one
transaction, or claim the row, call the transfer connector outside the transaction,
then `apply_item_outcome`) → `poll_bulk_items` every 5 min for rows the institution
left pending.

**Security notes**
- Cells starting with `= + - @` are rejected on upload and apostrophe-escaped in the
  results export (CSV/formula injection).
- Role checks are enforced in `services`, not only in the views; a forged POST from
  the maker to approve their own batch is refused.
- Batches are scoped to `request.merchant`; another merchant's batch URL is a 404.
- Upload size is checked before the file is read; the parser caps rows.

**Tests:** `apps/bulk/tests/test_bulk.py` — parsing (CSV with BOM/loose headers,
XLSX round-trip, bad files), validation of each failure mode, fee schedule, the full
mixed-outcome flow with ledger invariants, pending→poller resolution, maker-checker
and sole-approver rules, insufficient balance (submit and race at approval), roles
and licence gating, duplicate-file guard, export injection safety, and the portal
(upload, review, forged approve refused, approve, results, merchant isolation).
