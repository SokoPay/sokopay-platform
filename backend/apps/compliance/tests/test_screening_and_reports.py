"""Sanctions/PEP screening, watchlist imports, alert workflow and FIC reports."""

import json

import pytest
from django.contrib.auth import get_user_model

from apps.compliance import alerts, reports, screening, watchlists
from apps.compliance.models import Alert, LargeTransactionReport, ScreeningMatch, WatchlistEntry
from apps.kyc.limits import profile_for

User = get_user_model()
pytestmark = pytest.mark.django_db

SANCTIONS_CSV = b"""name,aliases,dob,nationality,reference,kind
Kwame Asante Mensah,K. A. Mensah;Kwame Mensa,1970-01-01,GH,GH-001,sanctions
Abena Serwaa Boateng,,1980-05-05,GH,PEP-1,pep
"""

UN_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL>
  <REFERENCE_NUMBER>QDi.001</REFERENCE_NUMBER><FIRST_NAME>JOHN</FIRST_NAME><SECOND_NAME>EXAMPLE</SECOND_NAME>
  <THIRD_NAME>DOE</THIRD_NAME><NATIONALITY><VALUE>Testland</VALUE></NATIONALITY>
  <INDIVIDUAL_ALIAS><ALIAS_NAME>Johnny Doe</ALIAS_NAME></INDIVIDUAL_ALIAS>
  <INDIVIDUAL_DATE_OF_BIRTH><YEAR>1965</YEAR></INDIVIDUAL_DATE_OF_BIRTH>
</INDIVIDUAL></INDIVIDUALS><ENTITIES><ENTITY><REFERENCE_NUMBER>QDe.9</REFERENCE_NUMBER>
  <FIRST_NAME>BAD SHELL TRADING COMPANY</FIRST_NAME></ENTITY></ENTITIES></CONSOLIDATED_LIST>"""


@pytest.fixture
def officer(db):
    return User.objects.create_user(phone="+233200000001", full_name="Officer A", user_type="staff")


@pytest.fixture
def officer2(db):
    return User.objects.create_user(phone="+233200000002", full_name="Officer B", user_type="staff")


@pytest.fixture
def lists(db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        watchlists.import_list(source="gh", fmt="csv", filename="gh.csv", data=SANCTIONS_CSV)


def test_fuzzy_matching_handles_order_spelling_and_accents():
    n = screening.normalise
    assert screening.similarity(n("Kwame Asante Mensah"), n("MENSAH, Kwame Asante")) == 1.0
    assert screening.similarity(n("Kwame Asante Mensah"), n("Kwame Asante Mensa")) >= 0.88
    assert screening.similarity(n("José Gonçalves"), n("Jose Goncalves")) == 1.0
    assert screening.similarity(n("Kwame Asante Mensah"), n("Ama Serwaa Owusu")) < 0.6


def test_import_replaces_source_and_rescreens_existing_customers(django_capture_on_commit_callbacks):
    hit = User.objects.create_user(phone="+233244000001", full_name="Kwame Asante Mensah")
    clean = User.objects.create_user(phone="+233244000002", full_name="Ama Owusu")
    with django_capture_on_commit_callbacks(execute=True):
        watchlists.import_list(source="gh", fmt="csv", filename="gh.csv", data=SANCTIONS_CSV)
    assert WatchlistEntry.objects.filter(source="gh").count() == 2
    assert ScreeningMatch.objects.filter(user=hit, entry_kind="sanctions").exists()
    assert not ScreeningMatch.objects.filter(user=clean).exists()
    # Sanctions hit → automatic hold.
    assert profile_for(hit).frozen
    # Re-import replaces (no duplicates).
    with django_capture_on_commit_callbacks(execute=True):
        watchlists.import_list(source="gh", fmt="csv", filename="gh2.csv", data=SANCTIONS_CSV)
    assert WatchlistEntry.objects.filter(source="gh").count() == 2


def test_pep_match_alerts_without_hold(lists):
    u = User.objects.create_user(phone="+233244000003", full_name="Abena Serwaa Boateng")
    screening.screen_user(u)
    a = Alert.objects.get(user=u, rule="PEP_MATCH")
    assert a.severity == "medium" and not profile_for(u).frozen


def test_single_names_are_not_screened(lists):
    u = User.objects.create_user(phone="+233244000004", full_name="Kwame")
    assert screening.screen_user(u) == []


def test_review_clear_releases_hold_confirm_keeps_it(lists, officer):
    u = User.objects.create_user(phone="+233244000005", full_name="Kwame Mensa")      # alias match
    m = screening.screen_user(u)[0]
    assert profile_for(u).frozen
    with pytest.raises(ValueError):
        screening.review(m, actor=officer, confirmed=False, reason="")
    screening.review(m, actor=officer, confirmed=False, reason="Different date of birth (1995).")
    assert not profile_for(u).frozen
    m.alert.refresh_from_db()
    assert m.alert.status == "closed_no_action"

    v = User.objects.create_user(phone="+233244000006", full_name="Kwame Asante Mensah")
    m2 = screening.screen_user(v)[0]
    screening.review(m2, actor=officer, confirmed=True, reason="DOB and nationality match.")
    m2.alert.refresh_from_db()
    assert m2.alert.status == "escalated" and profile_for(v).frozen


def test_kyc_verification_triggers_screening(lists, settings):
    from apps.kyc import services as kyc
    settings.KYC_IDENTITY_PROVIDER = "mock"
    u = User.objects.create_user(phone="+233244000007", full_name="Kwame Asante Mensah")
    kyc.upgrade_to_verified(u, "GHA-123456789-1")
    assert ScreeningMatch.objects.filter(user=u).exists()


def test_un_xml_and_ofac_parsers():
    un = watchlists.parse_un_xml(UN_XML, "un")
    names = {e.name for e in un}
    assert names == {"JOHN EXAMPLE DOE", "BAD SHELL TRADING COMPANY"}
    john = next(e for e in un if e.name.startswith("JOHN"))
    assert john.aliases == ["Johnny Doe"] and john.dob == "1965" and john.reference == "QDi.001"
    ofac = watchlists.parse_ofac_csv(b'36,"DOE, John Example","individual","SDGT"\n37,"ACME SHIPPING","-0-"\n', "ofac")
    assert [e.name for e in ofac] == ["John Example DOE", "ACME SHIPPING"]
    with pytest.raises(watchlists.WatchlistError):
        watchlists.parse_un_xml(b"<not xml", "un")
    with pytest.raises(watchlists.WatchlistError):
        watchlists.import_list(source="gh", fmt="csv", filename="x.csv", data=b"name\n")       # empty → refused


def test_xml_bombs_are_refused():
    bomb = b"""<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><l>&b;</l>"""
    with pytest.raises(watchlists.WatchlistError):
        watchlists.parse_un_xml(bomb, "un")


def test_alert_workflow_requires_reasons(officer):
    u = User.objects.create_user(phone="+233244000008", full_name="Test")
    a = alerts.raise_alert(rule="VELOCITY_OUT", title="t", severity="medium", subject_kind="customer",
                           subject_id=str(u.id), subject_label="Test", user=u, evidence={"x": 1})
    alerts.assign(a, actor=officer)
    assert a.status == "investigating"
    alerts.place_hold(a, actor=officer, reason="Investigating")
    assert profile_for(u).frozen
    with pytest.raises(ValueError):
        alerts.release_hold(a, actor=officer, reason="")
    alerts.release_hold(a, actor=officer, reason="Customer explained: salary")
    with pytest.raises(ValueError):
        alerts.close(a, actor=officer, reason=" ")
    alerts.close(a, actor=officer, reason="Legitimate salary payments")
    kinds = list(a.notes.values_list("kind", flat=True))
    assert kinds == ["system", "assign", "hold", "release", "status"]           # full audit trail


def test_str_four_eyes_and_filing(officer, officer2):
    u = User.objects.create_user(phone="+233244000009", full_name="Subject Person")
    a = alerts.raise_alert(rule="PASS_THROUGH", title="t", severity="high", subject_kind="customer",
                           subject_id=str(u.id), subject_label="Subject", user=u, evidence={"x": 1})
    with pytest.raises(reports.ReportError):
        reports.draft_str(alerts=[a], prepared_by=officer, narrative="too short")
    r = reports.draft_str(alerts=[a], prepared_by=officer,
                          narrative="Customer received GH₵3,000 from 12 unrelated senders and moved it out within minutes.")
    with pytest.raises(reports.ReportError):
        reports.approve_str(r, approver=officer)                    # same officer
    with pytest.raises(reports.ReportError):
        reports.mark_filed(r, actor=officer2, fic_reference="FIC-1")  # not approved yet
    reports.approve_str(r, approver=officer2)
    reports.mark_filed(r, actor=officer2, fic_reference="FIC-2026-0001")
    a.refresh_from_db()
    assert a.status == "closed_reported"
    body = json.loads(reports.export_str_json(r))
    assert body["subject"]["phone"] == u.phone and body["fic_reference"] == "FIC-2026-0001"


def test_large_txn_export_marks_rows(db):
    from django.utils import timezone
    u = User.objects.create_user(phone="+233244000010", full_name="Big Mover")
    LargeTransactionReport.objects.create(user=u, entry_id="e1", kind="cash_in", direction="in",
                                          amount_minor=60_000_00, occurred_at=timezone.now())
    out = reports.export_large_txns_csv().decode("utf-8-sig")
    assert "60000.00" in out and u.phone in out
    assert reports.export_large_txns_csv().decode("utf-8-sig").count("\n") == 1     # header only now
