"""
Files the portal hands out: the sample templates merchants fill in, and the results
file for a batch.

Every text cell is passed through `safe_cell`, which neutralises spreadsheet formula
injection: a value like `=HYPERLINK(...)` or `-2+3|cmd` in a recipient's name would
otherwise execute when the merchant opens the results in Excel.
"""

from __future__ import annotations

import csv
import io

from apps.common.money import Money

from .models import BulkPayout

SAMPLE_HEADERS = ["name", "destination_type", "institution", "account", "amount", "narrative"]
SAMPLE_ROWS = [
    ["Ama Mensah", "momo", "mtn", "0241234567", "850.00", "October salary"],
    ["Kofi Boateng", "momo", "telecel", "0201234567", "1200.50", "October salary"],
    ["Yaw Darko", "momo", "at", "0261234567", "700.00", "October salary"],
    ["Akosua Owusu", "sokopay", "", "0551234567", "600.00", "October salary"],
    ["Esi Quaye", "wallet", "zeepay", "0271234567", "450.00", "Commission"],
    ["Kwame Asante", "bank", "GCB", "1234567890123", "2000.00", "Supplier invoice 118"],
]
SAMPLE_NOTES = [
    "destination_type: sokopay (SokoPay wallet), momo (mobile money), bank, or wallet (other fintech).",
    "institution: mtn / telecel / at for momo; the bank code for bank; gmoney / zeepay for wallet; blank for sokopay.",
    "account: phone number (0241234567) for sokopay/momo/wallet; account number for bank.",
    "amount: in cedis with up to 2 decimals, e.g. 850.00. Max GH₵50,000 per row.",
]


def safe_cell(value) -> str:
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def sample_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(SAMPLE_HEADERS)
    for row in SAMPLE_ROWS:
        w.writerow(row)
    return ("﻿" + buf.getvalue()).encode("utf-8")      # BOM so Excel opens it as UTF-8


def sample_xlsx() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Payments"
    ws.append(SAMPLE_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in SAMPLE_ROWS:
        ws.append(row)
    for col, width in zip("ABCDEF", (22, 16, 12, 18, 12, 28)):
        ws.column_dimensions[col].width = width
    notes = wb.create_sheet("How to fill this in")
    for line in SAMPLE_NOTES:
        notes.append([line])
    notes.column_dimensions["A"].width = 110
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def results_csv(batch: BulkPayout) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["row", "name", "destination_type", "institution", "account", "amount",
                "fee", "status", "detail", "reference"])
    for item in batch.items.all():
        w.writerow([
            item.row_number,
            safe_cell(item.recipient_name),
            item.destination_type,
            safe_cell(item.institution),
            safe_cell(item.account),
            f"{Money(item.amount_minor, 'GHS').major:.2f}" if item.amount_minor else "",
            f"{Money(item.fee_minor, 'GHS').major:.2f}",
            item.status,
            safe_cell(item.error),
            safe_cell(item.provider_ref),
        ])
    return ("﻿" + buf.getvalue()).encode("utf-8")
