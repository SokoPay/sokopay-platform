"""
Loading sanctions / PEP lists from files a compliance officer provides.

Formats:
  un_xml    UN Security Council consolidated list (consolidated.xml)
  ofac_csv  US Treasury OFAC SDN list (sdn.csv — no header row)
  csv       generic: header row with name, aliases (";"-separated), dob, nationality,
            reference, kind (sanctions|pep). Use for Ghana domestic lists and PEP lists.

XML is parsed with defusedxml (no entity expansion / external entities). An import
REPLACES all entries of that source atomically, then everyone is rescreened.
"""

from __future__ import annotations

import csv
import io

from defusedxml import ElementTree as SafeET
from django.db import transaction
from apps.common.audit import audited

from .models import WatchlistEntry, WatchlistImport
from .screening import normalise

MAX_FILE_BYTES = 50 * 1024 * 1024


class WatchlistError(ValueError):
    pass


def _entry(source, kind, name, aliases=(), dob="", nationality="", reference=""):
    name = " ".join((name or "").split())
    aliases = [" ".join(a.split()) for a in aliases if a and a.strip()]
    norms = sorted({n for n in (normalise(x) for x in [name, *aliases]) if n})
    return WatchlistEntry(source=source, kind=kind, name=name[:255], aliases=aliases[:50],
                          normalised=norms, dob=dob[:32], nationality=nationality[:64],
                          reference=reference[:64])


def parse_un_xml(data: bytes, source: str) -> list[WatchlistEntry]:
    try:
        root = SafeET.fromstring(data)
    except Exception as exc:
        raise WatchlistError("Not a valid UN consolidated list XML file.") from exc

    def text(el, tag):
        f = el.find(tag)
        return (f.text or "").strip() if f is not None and f.text else ""

    out = []
    for ind in root.iter("INDIVIDUAL"):
        name = " ".join(filter(None, (text(ind, t) for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME"))))
        aliases = [text(a, "ALIAS_NAME") for a in ind.iter("INDIVIDUAL_ALIAS")]
        dob_el = ind.find("INDIVIDUAL_DATE_OF_BIRTH")
        dob = (text(dob_el, "DATE") or text(dob_el, "YEAR")) if dob_el is not None else ""
        nat_el = ind.find("NATIONALITY")
        nat = text(nat_el, "VALUE") if nat_el is not None else ""
        if name:
            out.append(_entry(source, "sanctions", name, aliases, dob, nat, text(ind, "REFERENCE_NUMBER")))
    for ent in root.iter("ENTITY"):
        name = text(ent, "FIRST_NAME")
        aliases = [text(a, "ALIAS_NAME") for a in ent.iter("ENTITY_ALIAS")]
        if name:
            out.append(_entry(source, "sanctions", name, aliases, reference=text(ent, "REFERENCE_NUMBER")))
    return out


def parse_ofac_csv(data: bytes, source: str) -> list[WatchlistEntry]:
    out = []
    for row in csv.reader(io.StringIO(data.decode("utf-8", "replace"))):
        if len(row) < 2 or not row[1].strip() or row[1].strip() == "-0-":
            continue
        name = row[1].strip()
        if "," in name and (len(row) < 3 or row[2].strip().lower() == "individual"):
            last, _, first = name.partition(",")              # "SURNAME, Given Names"
            name = f"{first.strip()} {last.strip()}"
        out.append(_entry(source, "sanctions", name, reference=row[0].strip()))
    return out


def parse_generic_csv(data: bytes, source: str) -> list[WatchlistEntry]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig", "replace")))
    if not reader.fieldnames or "name" not in [f.strip().lower() for f in reader.fieldnames]:
        raise WatchlistError("CSV needs a header row with at least a 'name' column.")
    out = []
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("name"):
            continue
        kind = row.get("kind") or ("pep" if source == "pep" else "sanctions")
        if kind not in ("sanctions", "pep"):
            raise WatchlistError(f"Unknown kind {kind!r} (use sanctions or pep).")
        out.append(_entry(source, kind, row["name"], row.get("aliases", "").split(";"),
                          row.get("dob", ""), row.get("nationality", ""), row.get("reference", "")))
    return out


PARSERS = {"un_xml": parse_un_xml, "ofac_csv": parse_ofac_csv, "csv": parse_generic_csv}


@audited("watchlist.import", fields=("source", "filename"))
def import_list(*, source: str, fmt: str, filename: str, data: bytes, actor=None) -> WatchlistImport:
    if source not in WatchlistEntry.Source.values:
        raise WatchlistError(f"Unknown source {source!r}.")
    if fmt not in PARSERS:
        raise WatchlistError(f"Unknown format {fmt!r}.")
    if len(data) > MAX_FILE_BYTES:
        raise WatchlistError("File is too large.")
    entries = PARSERS[fmt](data, source)
    if not entries:
        raise WatchlistError("No names found in that file — nothing was changed.")
    with transaction.atomic():
        WatchlistEntry.objects.filter(source=source).delete()
        WatchlistEntry.objects.bulk_create(entries, batch_size=1000)
        record = WatchlistImport.objects.create(source=source, filename=filename[:255],
                                                entries=len(entries), imported_by=actor)

        def _rescreen():
            from .tasks import rescreen_all
            rescreen_all.delay()

        transaction.on_commit(_rescreen, robust=True)   # a queue outage must not fail the committed action
    return record
