# 3. Traceability

Traceability means any money movement, decision or complaint can be followed from start to end: who started it, which systems it touched, and where the money went.

## The thread that ties it together

```
phone app ──X-Request-ID──▶ API ──▶ log lines (JSON, request_id)
                                 ├─▶ ledger JournalEntry.request_id   (every money movement)
                                 ├─▶ AuditEvent.request_id             (every privileged action)
                                 └─▶ partner call  (our reference: SP-/XB-/LS-/REFUND-/SETTLE-)
partner callback ──▶ RailEvent (raw, signature result) ──▶ re-query ──▶ same records
```

Search the staff **Audit** page, or the logs, for a request ID to see everything one request did.

## Controls in place

| Control | Where | Evidence |
|---|---|---|
| **Public references** on everything a customer or partner sees: SP- payments, XB- cross-border, LS- orders, AG- agent transactions, REFUND- and SETTLE- payouts | payments, wallet, agents, merchants | reference tests |
| **Every ledger entry** carries a narrative, a business reference (type and ID), an idempotency key and now the **request ID** *(new, R3)* | `apps/ledger` | `test_audit.py::test_ledger_entries_carry_the_request_id` |
| **Request ID** on every HTTP request and response, every production log line and every audit event. The apps send it too *(new, R3)* | `apps/common/audit.py`, `api_client.dart` | `test_audit.py` |
| **Raw partner callbacks stored** with the signature result before any processing | `payments.RailEvent` | payments tests |
| **Webhook delivery log:** each attempt, status code and error | `merchants.WebhookDelivery`, portal Webhooks page | `test_webhooks.py` |
| **Notification log:** every alert is stored in the inbox, with push and SMS-backup timestamps | `notifications.Notification` | push and SMS tests |
| **Case history:** AML alerts keep their notes, holds and decisions; disputes keep both sides and the decision; refunds link to the payment and dispute; a settlement links to its approver | compliance, merchants | compliance and dispute tests |
| **Statements** rebuilt from the ledger (opening, every movement, closing) | `apps/ledger/statements.py` | `test_statements.py` |
| **Agent transactions** keep the agent, customer and cash-out request chain | `apps/agents` | agent tests |

## Gaps and actions

| Priority | Gap | Action |
|---|---|---|
| Medium | **Background jobs don't carry the original request ID**: Celery tasks such as push delivery, webhook delivery and pollers log without it | Pass the request ID in Celery task headers and restore it in the worker. The ledger entry already links the business reference. |
| Medium | **Partner-side IDs differ per partner:** we store `rail_ref`, but each partner's statement uses its own fields | Map statement fields per partner when the Korba or Nsano spec arrives. Reconciliation already matches on `rail_ref`. |
| Low | **Customer-facing error messages don't show the request ID** | Show a short "Ref" on error screens so support can find it; the ID is already sent. |
