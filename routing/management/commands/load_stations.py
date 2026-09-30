import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import FuelStation
from routing.services.stations import clean_stations

DEFAULT_CSV = Path(settings.BASE_DIR) / "data" / "fuel-prices-for-be-assessment.csv"


class Command(BaseCommand):
    help = "Load cleaned fuel stations from the CSV (idempotent: replaces all rows)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to the CSV file.")

    def handle(self, *args, **options):
        path: Path = options["csv"]
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")
        with path.open(newline="", encoding="utf-8-sig") as fh:
            stations, stats = clean_stations(csv.DictReader(fh))

        # Truncate + bulk_create in one transaction: re-runs never duplicate, and a
        # failure leaves the previous data intact. Coordinates are refilled by geocoding.
        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(
                [FuelStation(**s) for s in stations], batch_size=500
            )

        self.stdout.write(
            f"Rows read:            {stats.rows_read}\n"
            f"Dropped non-US:       {stats.dropped_non_us}\n"
            f"Dropped invalid:      {stats.dropped_invalid}\n"
            f"Rows after dedupe:    {stats.after_dedupe}\n"
            f"Stations saved:       {FuelStation.objects.count()}"
        )
