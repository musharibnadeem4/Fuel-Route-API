"""Offline US place lookup (city, state -> lat/lng). Pure functions, no Django imports.

The lookup is built once from GeoNames US postal data (zip centroids averaged per
city+state), saved to routing/data/us_places.csv and then only ever read locally.
"""
import csv
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .cleaning import US_STATES

PLACES_CSV = Path(__file__).resolve().parent.parent / "data" / "us_places.csv"
GEONAMES_URL = "https://download.geonames.org/export/zip/US.zip"

Coord = tuple[float, float]

_ABBREVIATIONS = {"ft": "fort", "st": "saint", "ste": "sainte", "mt": "mount"}
_PARENTHETICAL = re.compile(r"\([^)]*\)")
_TOWNSHIP_SUFFIX = re.compile(r"\s+(township|twp)$")
_LATLNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class PlaceNotFoundError(ValueError):
    """Raised when a place string cannot be resolved to coordinates."""


@dataclass(frozen=True)
class Place:
    """A resolved location with a canonical display name."""

    name: str
    lat: float
    lng: float


def normalize_city(name: str) -> str:
    """Lowercase, drop accents/punctuation/parentheticals and expand Ft./St./Mt.

    "St. Louis" and "Saint Louis" both become "saint louis"; "O'Fallon" -> "ofallon".
    """
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = _PARENTHETICAL.sub(" ", text.lower())
    text = re.sub(r"['.]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(_ABBREVIATIONS.get(t, t) for t in text.split())


def city_keys(name: str) -> list[str]:
    """Lookup keys for a city, most specific first (township suffix stripped last)."""
    key = normalize_city(name)
    keys = [key]
    stripped = _TOWNSHIP_SUFFIX.sub("", key)
    if stripped != key and stripped:
        keys.append(stripped)
    return keys


@dataclass(frozen=True)
class PlaceIndex:
    """In-memory lookup of city centroids plus a centroid per state."""

    cities: dict[tuple[str, str], Coord]
    states: dict[str, Coord]
    compact: dict[tuple[str, str], Coord] = field(default_factory=dict)  # spaces removed

    def find(self, city: str, state: str) -> Coord | None:
        """Return the centroid for a city, trying normalized variants; None if unknown."""
        state = state.strip().upper()
        for key in city_keys(city):
            if (key, state) in self.cities:
                return self.cities[(key, state)]
        for key in city_keys(city):  # "De Forest" vs "DeForest"
            if (key.replace(" ", ""), state) in self.compact:
                return self.compact[(key.replace(" ", ""), state)]
        return None

    def state_centroid(self, state: str) -> Coord | None:
        """Mean of the state's city centroids; used as a last-resort approximation."""
        return self.states.get(state.strip().upper())


def build_places(geonames_lines: Iterable[str]) -> list[tuple[str, str, float, float]]:
    """Average GeoNames zip centroids per (city, state) -> sorted (city, state, lat, lng)."""
    sums: dict[tuple[str, str], list] = {}
    for line in geonames_lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 11 or parts[4] not in US_STATES:
            continue
        key = (normalize_city(parts[2]), parts[4])
        entry = sums.setdefault(key, [parts[2], 0.0, 0.0, 0])
        entry[1] += float(parts[9])
        entry[2] += float(parts[10])
        entry[3] += 1
    return sorted(
        (name, state, round(lat / n, 5), round(lng / n, 5))
        for (_, state), (name, lat, lng, n) in sums.items()
    )


def write_places_csv(rows: Iterable[tuple[str, str, float, float]], path: Path | None = None) -> None:
    """Write the lookup CSV (city,state,lat,lng); defaults to the bundled PLACES_CSV."""
    path = path or PLACES_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["city", "state", "lat", "lng"])
        writer.writerows(rows)


def index_from_rows(rows: Iterable[dict[str, str]]) -> PlaceIndex:
    """Build a PlaceIndex from dict rows with city/state/lat/lng keys."""
    cities: dict[tuple[str, str], Coord] = {}
    compact: dict[tuple[str, str], Coord] = {}
    by_state: dict[str, list[Coord]] = {}
    for row in rows:
        coord = (float(row["lat"]), float(row["lng"]))
        key = normalize_city(row["city"])
        cities[(key, row["state"])] = coord
        compact.setdefault((key.replace(" ", ""), row["state"]), coord)
        by_state.setdefault(row["state"], []).append(coord)
    states = {
        st: (sum(c[0] for c in pts) / len(pts), sum(c[1] for c in pts) / len(pts))
        for st, pts in by_state.items()
    }
    return PlaceIndex(cities, states, compact)


def load_index(path: Path | None = None) -> PlaceIndex:
    """Load the lookup from CSV (no network); defaults to the bundled PLACES_CSV."""
    with (path or PLACES_CSV).open(newline="", encoding="utf-8") as fh:
        return index_from_rows(csv.DictReader(fh))


@lru_cache(maxsize=1)
def get_index() -> PlaceIndex:
    """Process-wide cached default index."""
    return load_index()


def _display_city(city: str) -> str:
    """Collapse whitespace and title-case all-lower/all-upper names; keep mixed case (McKinney)."""
    city = " ".join(city.split())
    return city.title() if city.islower() or city.isupper() else city


def lookup_place(text: str, index: PlaceIndex | None = None) -> Place:
    """Resolve "City, ST" or "lat,lng" to a Place using only the local lookup.

    Raises PlaceNotFoundError with a clear message for unparseable or unknown places.
    """
    text = (text or "").strip()
    if not text:
        raise PlaceNotFoundError("Empty location.")
    match = _LATLNG.match(text)
    if match:
        lat, lng = float(match.group(1)), float(match.group(2))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise PlaceNotFoundError(f"Coordinates out of range: {text!r}.")
        return Place(f"{lat:g}, {lng:g}", lat, lng)
    city, sep, state = (p.strip() for p in text.rpartition(","))
    if not sep or not city or state.upper() not in US_STATES:
        raise PlaceNotFoundError(
            f"Could not parse {text!r}; use 'City, ST' (US state code) or 'lat,lng'."
        )
    coord = (index or get_index()).find(city, state)
    if coord is None:
        raise PlaceNotFoundError(f"Unknown US place: {city!r}, {state.upper()}.")
    return Place(f"{_display_city(city)}, {state.upper()}", coord[0], coord[1])


def resolve_place(text: str, index: PlaceIndex | None = None) -> Coord:
    """Resolve "City, ST" or "lat,lng" to (lat, lng); see lookup_place for errors."""
    place = lookup_place(text, index)
    return place.lat, place.lng
