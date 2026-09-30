import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import FuelStation
from routing.services.cleaning import clean_stations

DEFAULT_CSV = Path(settings.BASE_DIR) / "data" / "fuel-prices-for-be-assessment.csv"


UPSERT_FIELDS = ["name", "address", "city", "state", "price"]


class Command(BaseCommand):
    help = "Load cleaned fuel stations from the CSV (idempotent upsert; keeps coordinates)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to the CSV file.")

    def handle(self, *args, **options):
        path: Path = options["csv"]
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")
        with path.open(newline="", encoding="utf-8-sig") as fh:
            stations, stats = clean_stations(csv.DictReader(fh))

        # Upsert by opis_id so re-runs never duplicate and geocoded lat/lng (not in
        # update_fields) survive. Stations missing from the CSV are then removed.
        with transaction.atomic():
            FuelStation.objects.bulk_create(
                [FuelStation(**s) for s in stations],
                batch_size=500,
                update_conflicts=True,
                unique_fields=["opis_id"],
                update_fields=UPSERT_FIELDS,
            )
            keep = {s["opis_id"] for s in stations}
            stale = [i for i in FuelStation.objects.values_list("opis_id", flat=True) if i not in keep]
            for i in range(0, len(stale), 500):
                FuelStation.objects.filter(opis_id__in=stale[i : i + 500]).delete()

        self.stdout.write(
            f"Rows read:            {stats.rows_read}\n"
            f"Dropped non-US:       {stats.dropped_non_us}\n"
            f"Dropped invalid:      {stats.dropped_invalid}\n"
            f"Rows after dedupe:    {stats.after_dedupe}\n"
            f"Stale removed:       {len(stale)}
"
            f"Stations saved:       {FuelStation.objects.count()}"
        )
