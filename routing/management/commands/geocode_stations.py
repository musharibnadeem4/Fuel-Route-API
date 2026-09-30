import csv
import io
import zipfile
from collections import Counter
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import FuelStation
from routing.services import geocode

UNMATCHED_CSV = Path(settings.BASE_DIR) / "data" / "unmatched_cities.csv"


def download_places_csv(path: Path) -> None:
    """Fetch GeoNames US.zip and write the averaged city lookup CSV to `path`."""
    response = requests.get(geocode.GEONAMES_URL, timeout=120)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        lines = zf.read("US.txt").decode("utf-8").splitlines()
    geocode.write_places_csv(geocode.build_places(lines), path)


class Command(BaseCommand):
    help = "Assign lat/lng to every FuelStation from the local US places lookup."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true", help="Re-download the GeoNames lookup.")

    def handle(self, *args, **options):
        if options["refresh"] or not geocode.PLACES_CSV.exists():
            self.stdout.write("Downloading GeoNames US postal data...")
            download_places_csv(geocode.PLACES_CSV)
            geocode.get_index.cache_clear()
        index = geocode.load_index()

        stations = list(FuelStation.objects.all())
        unmatched: Counter[tuple[str, str]] = Counter()
        matched = 0
        for st in stations:
            coord = index.find(st.city, st.state)
            st.geo_approx = coord is None
            if coord is None:
                unmatched[(st.city, st.state)] += 1
                coord = index.state_centroid(st.state)
            else:
                matched += 1
            st.lat, st.lng = coord if coord else (None, None)

        with transaction.atomic():
            FuelStation.objects.bulk_update(stations, ["lat", "lng", "geo_approx"], batch_size=500)
        self._write_unmatched(unmatched)
        self._report(len(stations), matched, unmatched)

    @staticmethod
    def _write_unmatched(unmatched: Counter) -> None:
        if not unmatched:
            UNMATCHED_CSV.unlink(missing_ok=True)
            return
        with UNMATCHED_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["city", "state", "stations"])
            writer.writerows((c, s, n) for (c, s), n in sorted(unmatched.items()))

    def _report(self, total: int, matched: int, unmatched: Counter) -> None:
        self.stdout.write(
            f"Stations total:               {total}\n"
            f"Stations matched:             {matched}\n"
            f"Stations unmatched (approx):  {sum(unmatched.values())}\n"
            f"Unmatched unique city/state:  {len(unmatched)}"
        )
        for (city, state), n in sorted(unmatched.items()):
            self.stdout.write(f"  {city}, {state} ({n})")
        if unmatched:
            self.stdout.write(f"Written to {UNMATCHED_CSV}")
