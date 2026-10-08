"""
Sanctions & PEP screening.

Names are normalised (case, accents, punctuation, word order) and compared with fuzzy
matching, because lists and Ghana Cards spell names differently ("Mohammed"/"Muhammad",
"ASANTE Kwame"/"Kwame Asante"). A score at/above AML_SCREENING_THRESHOLD creates a
ScreeningMatch for a human to confirm or clear:

  sanctions hit → alert SANCTIONS_MATCH (high) + automatic hold until reviewed
  PEP hit       → alert PEP_MATCH (medium): enhanced due diligence, no hold

When is someone screened?
  * when their Ghana Card name is verified (KYC upgrade) and when they change their name
  * merchants: their legal/trading name when submitted for review
  * everyone again whenever a list is (re)imported (rescreen_all)
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import defaultdict

from django.conf import settings
from django.db import IntegrityError, transaction
from apps.common.audit import audited

from .alerts import raise_alert
from .models import ScreeningMatch, Severity, WatchlistEntry

DEFAULT_THRESHOLD = 0.88
_STOP = {"mr", "mrs", "ms", "dr", "alhaji", "hajia", "nana", "the", "of", "and", "al", "el", "bin", "ibn"}


def threshold() -> float:
    return float(getattr(settings, "AML_SCREENING_THRESHOLD", DEFAULT_THRESHOLD))


def tokens(name: str) -> list[str]:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return [t for t in re.findall(r"[a-z]+", text) if t not in _STOP and len(t) > 1]


def normalise(name: str) -> str:
    return " ".join(sorted(tokens(name)))


def similarity(a: str, b: str) -> float:
    """0–1. Word order is ignored; each word is matched to its closest counterpart."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 0.0
    whole = difflib.SequenceMatcher(None, a, b).ratio()
    # Token-wise: every word of the shorter name must closely match a word of the longer.
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) >= 2:
        per = [max(difflib.SequenceMatcher(None, w, x).ratio() for x in long_) for w in short]
        token_score = sum(per) / len(per) * (0.9 + 0.1 * len(short) / len(long_))
    else:
        token_score = 0.0                      # a single word alone is too weak to match
    return round(max(whole, token_score), 3)


class _Index:
    """Token → entries, so we only compare against plausible candidates."""

    def __init__(self, entries):
        self.by_token = defaultdict(set)
        self.entries = {}
        for e in entries:
            self.entries[e.id] = e
            for norm in e.normalised:
                for t in norm.split():
                    if len(t) >= 3:
                        self.by_token[t[:4]].add(e.id)

    def candidates(self, norm: str):
        ids = set()
        for t in norm.split():
            ids |= self.by_token.get(t[:4], set())
        return [self.entries[i] for i in ids]


def build_index() -> _Index:
    return _Index(WatchlistEntry.objects.all().only("id", "name", "kind", "source", "normalised"))


def match_name(name: str, index: _Index | None = None) -> list[tuple[WatchlistEntry, float]]:
    norm = normalise(name)
    if len(norm.split()) < 2:            # can't responsibly screen a single word
        return []
    index = index or build_index()
    hits = []
    for e in index.candidates(norm):
        score = max((similarity(norm, n) for n in e.normalised), default=0.0)
        if score >= threshold():
            hits.append((e, score))
    return sorted(hits, key=lambda h: -h[1])


def _names_for(user) -> list[str]:
    names = {(user.full_name or "").strip()}
    profile = getattr(user, "kyc", None)
    if profile is not None and profile.verified_name:
        names.add(profile.verified_name.strip())
    return [n for n in names if n]


def screen_user(user, index: _Index | None = None) -> list[ScreeningMatch]:
    from apps.common.pagination import mask_phone
    created = []
    index = index or build_index()
    for name in _names_for(user):
        for entry, score in match_name(name, index):
            try:
                with transaction.atomic():
                    m = ScreeningMatch.objects.create(
                        user=user, entry=entry, entry_name=entry.name, entry_kind=entry.kind,
                        name_screened=name, score=score)
            except IntegrityError:
                continue                       # already raised for this person + entry
            sanctions = entry.kind == WatchlistEntry.Kind.SANCTIONS
            m.alert = raise_alert(
                rule="SANCTIONS_MATCH" if sanctions else "PEP_MATCH",
                title=("Possible sanctions list match" if sanctions else "Possible politically exposed person"),
                severity=Severity.HIGH if sanctions else Severity.MEDIUM,
                subject_kind="customer", subject_id=str(user.id),
                subject_label=f"{name} · {mask_phone(user.phone)}", user=user,
                evidence={"screened_name": name, "list_name": entry.name, "list": entry.source,
                          "list_reference": entry.reference if hasattr(entry, "reference") else "",
                          "score": score},
            )
            m.save(update_fields=["alert", "updated_at"])
            created.append(m)
    return created


def screen_merchant(merchant, index: _Index | None = None) -> int:
    """Screen a business's names. Hits are merchant alerts (no wallet to hold)."""
    index = index or build_index()
    hits = 0
    for name in {merchant.legal_name, merchant.trading_name} - {"", None}:
        for entry, score in match_name(name, index):
            hits += 1
            raise_alert(rule="SANCTIONS_MATCH" if entry.kind == "sanctions" else "PEP_MATCH",
                        title="Possible watchlist match (business)", severity=Severity.HIGH,
                        subject_kind="merchant", subject_id=str(merchant.id), subject_label=name,
                        user=None, evidence={"screened_name": name, "list_name": entry.name,
                                             "list": entry.source, "score": score})
    return hits


def rescreen_all() -> dict:
    """Everyone against the current lists (after an import). Returns counts."""
    from django.contrib.auth import get_user_model
    index = build_index()
    users = get_user_model().objects.filter(is_active=True).exclude(user_type="staff").select_related("kyc")
    new = 0
    for u in users.iterator(chunk_size=500):
        new += len(screen_user(u, index))
    return {"screened": users.count(), "new_matches": new}


@audited("screening.review", obj="match", fields=("confirmed", "reason"))
def review(match: ScreeningMatch, *, actor, confirmed: bool, reason: str) -> ScreeningMatch:
    """Confirm (same person: keep hold, escalate) or clear (different person)."""
    from django.utils import timezone

    from . import alerts
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Explain the decision (e.g. date of birth / nationality differs).")
    match.status = ScreeningMatch.Status.CONFIRMED if confirmed else ScreeningMatch.Status.CLEARED
    match.reviewed_by, match.reviewed_at = actor, timezone.now()
    match.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    alert = match.alert
    if alert is not None:
        if confirmed:
            alerts.escalate(alert, actor=actor, reason=f"Screening match confirmed: {reason}")
            if match.entry_kind == "sanctions" and alert.user is not None:
                alerts.place_hold(alert, actor=actor, reason="Confirmed sanctions match.")
        else:
            others_pending = ScreeningMatch.objects.filter(
                user=match.user, alert=alert, status=ScreeningMatch.Status.PENDING).exists()
            if not others_pending:
                if alert.rule == "SANCTIONS_MATCH" and alert.user is not None:
                    alerts.release_hold(alert, actor=actor, reason=f"Screening match cleared: {reason}")
                alerts.close(alert, actor=actor, reason=f"False positive: {reason}")
    return match
