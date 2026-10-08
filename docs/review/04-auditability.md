# 4. Auditability

Auditability means an internal auditor, the Bank of Ghana or the Financial Intelligence Centre can independently verify what happened, and nobody can quietly change the record afterwards.

## Controls in place

| Control | Where | Evidence |
|---|---|---|
| **Append-only ledger:** postings are never edited, corrections are new entries, the whole ledger always sums to zero, and an hourly integrity job compares cached balances with the postings | `apps/ledger` | ledger tests, `test_tasks.py` |
| **Audit log for privileged actions** *(new, R1)*: append-only in the model, and **blocked in the database on PostgreSQL** by a trigger. It records who, what, the object, a summary, data, request ID and IP. Exports are themselves audited | `apps/common/models.py`, migration `common/0002` | `test_audit.py` (including the Postgres trigger test) |
| **Four-eyes records** keep the maker and checker on settlements, float top-ups, price rules, STRs, dispute decisions and bulk approvals | each app's models | tests per feature |
| **Regulatory records:** STR with preparer, approver and FIC reference, kept as a snapshot; large-transaction reports; screening decisions with a reason | `apps/compliance` | compliance tests |
| **Reconciliation:** daily partner statement against our books; each break resolved with a note and the person's name | `apps/reconciliation`, Recon page | `test_ops_portal.py` |
| **Proof a backup is complete:** `db_fingerprint` checks row counts, a balance checksum and ledger integrity. A restore drill passed | `apps/ledger/management/commands/db_fingerprint.py` | `test_db_fingerprint.py`, docs/ENGINEERING-OPERATIONS.md |
| **Monthly regulatory figures,** with the export audited | `apps/compliance/regulatory.py` | `test_statements.py` |
| **Security events:** login lockouts, 2FA enrolments and resets, backup-code use | `portal.SecurityEvent` | portal security tests |
| **Timestamps** on everything; times in Africa/Accra on statements | models (`TimeStampedModel`) | n/a |

## Gaps and actions

| Priority | Gap | Action |
|---|---|---|
| High | **Audit log and logs live in the same database and account they describe.** A full database administrator could still alter them | Ship audit events and logs nightly to **S3 with Object Lock** (compliance mode, at least 5 years) in a separate AWS account the app can't delete from. |
| Medium | **Retention:** audit rows and logs have no automated archival or retention rule | Keep 5+ years in line with Act 1044 [VERIFY]. Archive to S3 and keep 13 months hot. |
| Medium | **Django admin edits** are logged in Django's own `LogEntry`, separately from `AuditEvent` | Make Django admin read-only for money and compliance models (most already are), or mirror `LogEntry` into `AuditEvent`. |
| Low | **Clock trust:** server time comes from AWS NTP; there's no signed timestamping | Acceptable for now. Revisit if an auditor requires trusted timestamps. |
