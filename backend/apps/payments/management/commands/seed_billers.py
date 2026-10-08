"""
Seed the biller and airtime/data catalogue.

Every entry routes through the Enhanced PSP partner ("rail") by default. To move a
biller to a direct integration once it's built, change its `connector` (e.g. to "ecg")
in the admin. The rail biller codes below are placeholders until the partner supplies
its catalogue. [VERIFY availability and codes per partner]

Run:  python manage.py seed_billers
"""

from django.core.management.base import BaseCommand

from apps.payments.models import Biller

BILLERS = [
    # code,             name,                                          category,      rail code,          network,   lookup
    # --- Electricity ---
    ("ECG_PREPAID",     "ECG Prepaid",                                 "electricity", "ecg_prepaid",      "",        True),
    ("ECG_POSTPAID",    "ECG Postpaid",                                "electricity", "ecg_postpaid",     "",        True),
    ("NEDCO",           "NEDCo (Northern Electricity)",                "electricity", "nedco",            "",        True),
    # --- Water ---
    ("GWCL",            "Ghana Water",                                 "water",       "gwcl",             "",        True),
    # --- TV ---
    ("DSTV",            "DStv",                                        "tv",          "dstv",             "",        True),
    ("GOTV",            "GOtv",                                        "tv",          "gotv",             "",        True),
    ("STARTIMES",       "StarTimes",                                   "tv",          "startimes",        "",        True),
    # --- Internet ---
    ("TELECEL_FIBRE",   "Telecel Fibre/Broadband (formerly Vodafone)", "internet",    "telecel_fibre",    "",        True),
    # --- Airtime ---
    ("MTN_AIRTIME",     "MTN Airtime",                                 "airtime",     "mtn_airtime",      "mtn",     False),
    ("TELECEL_AIRTIME", "Telecel Airtime",                             "airtime",     "telecel_airtime",  "telecel", False),
    ("AT_AIRTIME",      "AT Airtime (formerly AirtelTigo)",            "airtime",     "at_airtime",       "at",      False),
    # --- Data bundles ---
    ("MTN_DATA",        "MTN Data",                                    "data",        "mtn_data",         "mtn",     False),
    ("TELECEL_DATA",    "Telecel Data",                                "data",        "telecel_data",     "telecel", False),
    ("AT_DATA",         "AT Data (formerly AirtelTigo)",               "data",        "at_data",          "at",      False),
]


class Command(BaseCommand):
    help = "Seed billers, airtime and data products (all routed via the payment partner)."

    def handle(self, *args, **options):
        created = 0
        for code, name, category, rail_code, network, lookup in BILLERS:
            _, was_created = Biller.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "category": category,
                    "rail_biller_code": rail_code,
                    "network": network,
                    "supports_lookup": lookup,
                    "connector": "rail",
                    "is_active": True,
                },
            )
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(
            f"{len(BILLERS)} billers ready ({created} newly created)."
        ))
