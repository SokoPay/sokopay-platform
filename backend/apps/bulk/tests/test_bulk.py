"""
Bulk payouts: parsing, validation, maker-checker, money movement and the portal.

Money invariants checked throughout: postings always sum to zero; the merchant is
debited exactly amount + fees of the rows actually attempted; failed rows come back.
"""

import io

import pytest
from django.contrib.auth import get_user_model

from apps.bulk import exports, parser, services, validate
from apps.bulk.exceptions import BulkError, ParseError
from apps.bulk.fees import bulk_fee
from apps.bulk.models import BulkPayout
from apps.connectors.transfers import MockTransferConnector
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding
from apps.merchants.models import MerchantMember
from apps.notifications.models import Notification
from apps.portal.tests.helpers import PASSWORD, login_verified
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import RailStatus
from apps.wallet import services as wallet

User = get_user_model()
pytestmark = pytest.mark.django_db

CSV = (
    "Name,Type,Network,Phone,Amount (GHS),Note\n"
    "Ama Mensah,momo,mtn,0241234567,850.00,October salary\n"
    "Kofi Boateng,momo,telecel,0201234567,\"1,200.50\",October salary\n"
    "Akosua Owusu,sokopay,,0551234567,600.00,October salary\n"
    "Esi Quaye,wallet,zeepay,0271234567,450.00,Commission\n"
    "Kwame Asante,bank,GCB,1234567890123,2000.00,Supplier invoice 118\n"
)


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.INTEROP_ROUTES = {"momo": "mock", "bank": "mock", "wallet": "mock"}
    settings.PUSH_PROVIDER = "console"
    reset_rail_cache()
    MockRail.reset()
    MockTransferConnector.reset()
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()
    _enabled_set.cache_clear()


@pytest.fixture
def shop(db):
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner", password=PASSWORD)
    m = onboarding.create_merchant(owner=owner, legal_name="Ama Stores Ltd",
                                   business_type="registered", trading_name="Ama Stores")
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    post_entry("seed merchant balance",
               [debit(accounts.partner_clearing("mock"), 10_000_00),
                credit(accounts.merchant_payable(str(m.id)), 10_000_00)])
    return m


@pytest.fixture
def finance(shop):
    user = User.objects.create_user(phone="+233200000011", full_name="Finance", password=PASSWORD)
    MerchantMember.objects.create(merchant=shop, user=user, role=MerchantMember.Role.FINANCE)
    return user


@pytest.fixture
def akosua(db):
    """A SokoPay customer who appears in the CSV as a sokopay recipient."""
    return User.objects.create_user(phone="+233551234567", full_name="Akosua Owusu")


def _postings_balance():
    return sum(p.amount for p in Posting.objects.all())


def _payable(shop):
    return natural_balance_of(accounts.merchant_payable(str(shop.id)))


# --- parsing -------------------------------------------------------------------
def test_parse_csv_with_loose_headers_and_bom():
    rows = parser.parse("pay.csv", ("﻿" + CSV).encode())
    assert len(rows) == 5
    assert rows[0]["name"] == "Ama Mensah" and rows[0]["account"] == "0241234567"
    assert rows[1]["amount"] == "1,200.50" and rows[1]["_row"] == 2
    assert rows[2]["destination_type"] == "sokopay"


def test_parse_xlsx_roundtrip():
    rows = parser.parse("template.xlsx", exports.sample_xlsx())
    assert [r["name"] for r in rows][:2] == ["Ama Mensah", "Kofi Boateng"]
    assert rows[3]["destination_type"] == "sokopay"


def test_parse_rejects_bad_files():
    with pytest.raises(ParseError):
        parser.parse("pay.csv", b"foo,bar\n1,2\n")              # missing required columns
    with pytest.raises(ParseError):
        parser.parse("pay.pdf", b"%PDF")
    with pytest.raises(ParseError):
        parser.parse("pay.xlsx", b"not a zip")
    with pytest.raises(ParseError):
        parser.parse("pay.csv", b"x" * (parser.MAX_BYTES + 1))
    with pytest.raises(ParseError):
        parser.parse("pay.csv", b"name,phone,amount\n")          # header only
    too_many = "name,phone,amount\n" + "".join(f"P{i},024000{i:04d},1\n" for i in range(parser.MAX_ROWS + 1))
    with pytest.raises(ParseError):
        parser.parse("pay.csv", too_many.encode())


# --- validation ------------------------------------------------------------------
def test_validation_marks_each_problem(akosua):
    csv = (
        "name,type,network,phone,amount\n"
        "Good,momo,mtn,0241234567,10.00\n"
        "Bad phone,momo,mtn,12345,10.00\n"
        "Bad network,momo,glo,0241234567,10.00\n"
        "Bad amount,momo,mtn,0241234568,-5\n"
        "Three decimals,momo,mtn,0241234569,10.005\n"
        "Too big,momo,mtn,0241234570,60000\n"
        "=HYPERLINK(evil),momo,mtn,0241234571,10.00\n"
        "Dup,momo,mtn,0241234567,10.00\n"
        "No account,sokopay,,0209999999,10.00\n"
        "Akosua,sokopay,,0551234567,10.00\n"
        "Over limit,sokopay,,0551234567,4000.00\n"
        "Bank ok,bank,GCB,1234567890,10.00\n"
        "Bank no code,bank,,1234567890,10.00\n"
    )
    items = validate.validate_rows(parser.parse("v.csv", csv.encode()))
    by_name = {i.recipient_name: i for i in items}
    assert by_name["Good"].status == "valid" and by_name["Good"].account == "+233241234567"
    assert by_name["Good"].fee_minor == 50                      # 0.75% of 10.00 → floor 0.50
    assert "phone" in by_name["Bad phone"].error
    assert "Network" in by_name["Bad network"].error
    assert "Amount" in by_name["Bad amount"].error
    assert "Amount" in by_name["Three decimals"].error
    assert "limit" in by_name["Too big"].error
    assert "must not start" in by_name["=HYPERLINK(evil)"].error
    assert "Duplicate of row 1" in by_name["Dup"].error
    assert "No SokoPay account" in by_name["No account"].error
    assert by_name["Akosua"].status == "valid" and by_name["Akosua"].recipient_user == akosua
    assert by_name["Akosua"].fee_minor == 0
    assert "wallet limit" in by_name["Over limit"].error     # tier 0 max_txn GH₵3,000
    assert by_name["Bank ok"].status == "valid" and by_name["Bank ok"].institution == "GCB"
    assert "Bank code" in by_name["Bank no code"].error


def test_sokopay_rows_invalid_without_demi(settings, akosua):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_MEDIUM"
    _enabled_set.cache_clear()
    items = validate.validate_rows(parser.parse("v.csv", CSV.encode()))
    soko = next(i for i in items if i.destination_type == "sokopay")
    assert soko.status == "invalid" and "DEMI" in soko.error
    assert sum(i.status == "valid" for i in items) == 4


def test_fee_schedule():
    assert bulk_fee("sokopay", 100_000) == 0
    assert bulk_fee("momo", 10_00) == 50
    assert bulk_fee("momo", 850_00) == 638                      # 0.75% of 850 = 6.375 → 6.38
    assert bulk_fee("bank", 2_000_00) == 15_00


# --- full flow -------------------------------------------------------------------
def test_full_flow_with_mixed_outcomes(shop, finance, akosua, django_capture_on_commit_callbacks):
    owner = shop.owner
    MockTransferConnector.fail_for("+233271234567")             # Esi's wallet declines

    batch = services.create_batch(merchant=shop, created_by=owner, filename="oct.csv",
                                  data=CSV.encode(), note="October salaries")
    assert batch.status == BulkPayout.Status.DRAFT
    assert batch.total_count == 5 and batch.valid_count == 5 and batch.invalid_count == 0
    amount = 850_00 + 1_200_50 + 600_00 + 450_00 + 2_000_00
    fees = bulk_fee("momo", 850_00) + bulk_fee("momo", 1_200_50) + bulk_fee("wallet", 450_00) + bulk_fee("bank", 2_000_00)
    assert batch.total_amount_minor == amount and batch.total_fee_minor == fees
    assert _payable(shop) == 10_000_00                           # nothing moved yet

    services.submit(batch, by=owner)
    batch.refresh_from_db()
    assert batch.status == BulkPayout.Status.AWAITING_APPROVAL

    with pytest.raises(BulkError):                               # maker ≠ checker
        services.approve(batch, checker=owner)

    with django_capture_on_commit_callbacks(execute=True):
        services.approve(batch, checker=finance, note="ok")
    batch.refresh_from_db()
    assert batch.approved_by == finance and not batch.self_approved
    assert batch.status == BulkPayout.Status.COMPLETED_WITH_FAILURES
    assert batch.paid_count == 4 and batch.failed_count == 1

    items = {i.recipient_name: i for i in batch.items.all()}
    assert items["Esi Quaye"].status == "failed" and "declined" in items["Esi Quaye"].error
    assert items["Akosua Owusu"].status == "paid" and items["Akosua Owusu"].connector == "sokopay"
    assert items["Kwame Asante"].status == "paid" and items["Kwame Asante"].provider_ref.startswith("XFER-")

    # Money: merchant paid for 4 rows (+ their fees); Esi's amount+fee came back.
    esi_total = 450_00 + bulk_fee("wallet", 450_00)
    assert _payable(shop) == 10_000_00 - (amount + fees) + esi_total
    assert wallet.balance(akosua) == 600_00
    assert natural_balance_of(accounts.fee_revenue()) == fees - bulk_fee("wallet", 450_00)
    assert natural_balance_of(accounts.bulk_in_flight()) == 0    # nothing left in flight
    assert natural_balance_of(accounts.partner_clearing("mock")) == 10_000_00 - (850_00 + 1_200_50 + 2_000_00)
    assert _postings_balance() == 0

    # People were told.
    assert Notification.objects.filter(user=akosua, title="Money received").exists()
    assert Notification.objects.filter(user=finance, title="Bulk payment finished").exists()

    # Results file is injection-safe and complete.
    out = exports.results_csv(batch).decode("utf-8-sig")
    assert out.count("\r\n") == 6 and "declined" in out

    # Re-running the worker is a no-op (idempotent).
    with django_capture_on_commit_callbacks(execute=True):
        services.process_batch(batch.id)
    assert _postings_balance() == 0 and wallet.balance(akosua) == 600_00


def test_pending_item_resolved_by_poller(shop, finance, django_capture_on_commit_callbacks):
    MockTransferConnector.script(RailStatus.PENDING)
    csv = "name,type,network,phone,amount\nAma,momo,mtn,0241234567,100.00\n"
    batch = services.create_batch(merchant=shop, created_by=shop.owner, filename="p.csv", data=csv.encode())
    services.submit(batch, by=shop.owner)
    with django_capture_on_commit_callbacks(execute=True):
        services.approve(batch, checker=finance)
    batch.refresh_from_db()
    item = batch.items.get()
    assert batch.status == BulkPayout.Status.PROCESSING and item.status == "processing"
    assert natural_balance_of(accounts.bulk_in_flight()) == 100_00 + bulk_fee("momo", 100_00)

    MockTransferConnector.drive(item.provider_ref, RailStatus.SUCCEEDED)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.resolve_pending() == {"resolved": 1}
    batch.refresh_from_db()
    assert batch.status == BulkPayout.Status.COMPLETED and batch.paid_count == 1
    assert natural_balance_of(accounts.bulk_in_flight()) == 0
    assert _postings_balance() == 0


def test_sole_approver_may_self_approve_and_it_is_recorded(shop, django_capture_on_commit_callbacks):
    csv = "name,type,network,phone,amount\nAma,momo,mtn,0241234567,100.00\n"
    batch = services.create_batch(merchant=shop, created_by=shop.owner, filename="p.csv", data=csv.encode())
    services.submit(batch, by=shop.owner)
    with django_capture_on_commit_callbacks(execute=True):
        services.approve(batch, checker=shop.owner)
    batch.refresh_from_db()
    assert batch.self_approved and batch.status == BulkPayout.Status.COMPLETED


def test_submit_refuses_invalid_rows_unless_skipped(shop, finance):
    csv = ("name,type,network,phone,amount\n"
           "Ama,momo,mtn,0241234567,100.00\n"
           "Broken,momo,mtn,123,100.00\n")
    batch = services.create_batch(merchant=shop, created_by=shop.owner, filename="p.csv", data=csv.encode())
    assert batch.invalid_count == 1
    with pytest.raises(BulkError):
        services.submit(batch, by=shop.owner)
    services.submit(batch, by=shop.owner, exclude_invalid=True)
    batch.refresh_from_db()
    assert batch.status == BulkPayout.Status.AWAITING_APPROVAL and batch.exclude_invalid
    assert batch.items.get(recipient_name="Broken").status == "skipped"
    assert batch.needed_minor == 100_00 + bulk_fee("momo", 100_00)


def test_insufficient_balance_blocks_submit_and_approve(shop, finance):
    csv = "name,type,network,phone,amount\nBig,momo,mtn,0241234567,9999.00\n"
    batch = services.create_batch(merchant=shop, created_by=shop.owner, filename="p.csv", data=csv.encode())
    with pytest.raises(BulkError):                                # 9,999 + fee > 10,000
        services.submit(batch, by=shop.owner)

    csv = "name,type,network,phone,amount\nOk,momo,mtn,0241234567,9000.00\n"
    batch = services.create_batch(merchant=shop, created_by=shop.owner, filename="q.csv", data=csv.encode())
    services.submit(batch, by=shop.owner)
    # Balance drops before the checker gets to it (e.g. a settlement ran).
    post_entry("settle", [debit(accounts.merchant_payable(str(shop.id)), 5_000_00),
                          credit(accounts.partner_clearing("mock"), 5_000_00)])
    with pytest.raises(BulkError):
        services.approve(batch, checker=finance)
    batch.refresh_from_db()
    assert batch.status == BulkPayout.Status.AWAITING_APPROVAL and batch.reserved_minor == 0


def test_roles_and_licence(shop, finance, settings):
    cashier = User.objects.create_user(phone="+233200000012", full_name="Cashier")
    MerchantMember.objects.create(merchant=shop, user=cashier, role=MerchantMember.Role.CASHIER)
    csv = "name,type,network,phone,amount\nAma,momo,mtn,0241234567,100.00\n"
    with pytest.raises(BulkError):
        services.create_batch(merchant=shop, created_by=cashier, filename="p.csv", data=csv.encode())

    batch = services.create_batch(merchant=shop, created_by=shop.owner, filename="p.csv", data=csv.encode())
    services.submit(batch, by=shop.owner)
    with pytest.raises(BulkError):
        services.approve(batch, checker=cashier)
    stranger = User.objects.create_user(phone="+233200000013")
    with pytest.raises(BulkError):
        services.approve(batch, checker=stranger)

    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        services.create_batch(merchant=shop, created_by=shop.owner, filename="z.csv", data=csv.encode())
    with pytest.raises(CapabilityNotLicensed):
        services.approve(batch, checker=finance)


def test_duplicate_file_is_refused(shop):
    services.create_batch(merchant=shop, created_by=shop.owner, filename="oct.csv", data=CSV.encode())
    with pytest.raises(BulkError, match="already uploaded"):
        services.create_batch(merchant=shop, created_by=shop.owner, filename="oct-again.csv", data=CSV.encode())


def test_results_csv_neutralises_formulas(shop):
    csv = "name,type,network,phone,amount\n=cmd|' /C calc'!A0,momo,mtn,0241234567,10.00\n"
    batch = services.create_batch(merchant=shop, created_by=shop.owner, filename="p.csv", data=csv.encode())
    out = exports.results_csv(batch).decode("utf-8-sig")
    assert ",'=cmd|" in out and ",=cmd" not in out       # apostrophe-prefixed, so Excel shows text


# --- portal ----------------------------------------------------------------------
def _login(phone):
    return login_verified(phone)


def test_portal_upload_review_approve(shop, finance, akosua, django_capture_on_commit_callbacks):
    owner = _login(shop.owner.phone)
    page = owner.get("/dashboard/bulk/")
    assert page.status_code == 200 and b"Download sample .csv" in page.content

    sample = owner.get("/dashboard/bulk/sample.csv")
    assert sample.status_code == 200 and sample["Content-Type"].startswith("text/csv")
    assert b"destination_type" in sample.content
    assert owner.get("/dashboard/bulk/sample.xlsx").status_code == 200
    assert owner.get("/dashboard/bulk/sample.exe").status_code == 404

    upload = io.BytesIO(CSV.encode())
    upload.name = "october.csv"
    r = owner.post("/dashboard/bulk/", {"file": upload, "note": "October salaries"})
    batch = BulkPayout.objects.get(merchant=shop)
    assert r.status_code == 302 and r["Location"].endswith(f"/dashboard/bulk/{batch.id}/")

    detail = owner.get(f"/dashboard/bulk/{batch.id}/")
    assert b"Submit for approval" in detail.content and b"Ama Mensah" in detail.content
    owner.post(f"/dashboard/bulk/{batch.id}/", {"action": "submit"})
    batch.refresh_from_db()
    assert batch.status == BulkPayout.Status.AWAITING_APPROVAL

    # The maker sees no approve button and cannot approve by forging the POST.
    detail = owner.get(f"/dashboard/bulk/{batch.id}/")
    assert b"Approve &amp; pay" not in detail.content and b"cannot approve a batch you uploaded" in detail.content
    owner.post(f"/dashboard/bulk/{batch.id}/", {"action": "approve"})
    batch.refresh_from_db()
    assert batch.status == BulkPayout.Status.AWAITING_APPROVAL

    fin = _login(finance.phone)
    assert b"Approve &amp; pay" in fin.get(f"/dashboard/bulk/{batch.id}/").content
    with django_capture_on_commit_callbacks(execute=True):
        fin.post(f"/dashboard/bulk/{batch.id}/", {"action": "approve", "note": "checked"})
    batch.refresh_from_db()
    assert batch.status == BulkPayout.Status.COMPLETED and batch.paid_count == 5

    results = fin.get(f"/dashboard/bulk/{batch.id}/results.csv")
    assert results.status_code == 200 and b"paid" in results.content
    progress = fin.get(f"/dashboard/bulk/{batch.id}/progress/")
    assert b"5 paid" in progress.content


def test_portal_isolates_merchants_and_roles(shop, finance):
    other_owner = User.objects.create_user(phone="+233200000030", full_name="Other", password=PASSWORD)
    other = onboarding.create_merchant(owner=other_owner, legal_name="Other Ltd", business_type="sole_trader")
    batch = services.create_batch(merchant=shop, created_by=shop.owner, filename="p.csv",
                                  data="name,phone,amount\nA,0241234567,1.00\n".encode())
    c = _login(other_owner.phone)
    assert c.get(f"/dashboard/bulk/{batch.id}/").status_code == 404
    assert c.get(f"/dashboard/bulk/{batch.id}/results.csv").status_code == 404
    assert other.bulk_payouts.count() == 0

    cashier = User.objects.create_user(phone="+233200000031", password=PASSWORD)
    MerchantMember.objects.create(merchant=shop, user=cashier, role=MerchantMember.Role.CASHIER)
    page = _login(cashier.phone).get("/dashboard/bulk/")
    assert page.status_code == 200 and b"cannot upload payments" in page.content
    upload = io.BytesIO(b"name,phone,amount\nA,0241234567,1.00\n")
    upload.name = "x.csv"
    _login(cashier.phone).post("/dashboard/bulk/", {"file": upload})
    assert BulkPayout.objects.filter(merchant=shop).count() == 1
