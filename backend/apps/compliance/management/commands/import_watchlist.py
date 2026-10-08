"""
Load a sanctions / PEP list from a file, then rescreen everyone.

  python manage.py import_watchlist --source un  --format un_xml   --file consolidated.xml
  python manage.py import_watchlist --source ofac --format ofac_csv --file sdn.csv
  python manage.py import_watchlist --source pep --format csv      --file ghana_peps.csv

Download the official files yourself (UN: scsanctions.un.org; OFAC: treasury.gov); this
command never fetches anything from the internet.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.compliance.watchlists import PARSERS, WatchlistError, import_list


class Command(BaseCommand):
    help = "Import a sanctions/PEP watchlist file (replaces that source) and rescreen customers."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="un | ofac | eu | gh | pep | custom")
        parser.add_argument("--format", required=True, choices=sorted(PARSERS))
        parser.add_argument("--file", required=True)

    def handle(self, *args, source, format, file, **options):
        path = Path(file)
        if not path.is_file():
            raise CommandError(f"No such file: {file}")
        try:
            record = import_list(source=source, fmt=format, filename=path.name, data=path.read_bytes())
        except WatchlistError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Imported {record.entries} {source} entries; rescreen queued."))
