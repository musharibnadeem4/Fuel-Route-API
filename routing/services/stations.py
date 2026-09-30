"""Cleaning of the raw fuel-price CSV rows. Pure functions, no Django imports."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
PRICE_QUANT = Decimal("0.0001")


@dataclass
class CleanStats:
    """Row counts for each cleaning stage."""

    rows_read: int = 0
    dropped_non_us: int = 0
    dropped_invalid: int = 0
    after_dedupe: int = 0


def _text(value: object) -> str:
    return str(value or "").strip()


def _parse_row(row: Mapping[str, str]) -> dict | None:
    """Normalize one raw row, or return None if the ID or price is unusable."""
    try:
        opis_id = int(_text(row.get("OPIS Truckstop ID")))
        price = Decimal(_text(row.get("Retail Price"))).quantize(PRICE_QUANT)
    except (ValueError, InvalidOperation):
        return None
    return {
        "opis_id": opis_id,
        "name": _text(row.get("Truckstop Name")),
        "address": _text(row.get("Address")),
        "city": _text(row.get("City")).title(),
        "state": _text(row.get("State")).upper(),
        "price": price,
    }


def _merge(kept: dict, new: dict) -> None:
    """Fold `new` into `kept`: lowest price wins, empty fields are back-filled."""
    kept["price"] = min(kept["price"], new["price"])
    for field in ("name", "address", "city", "state"):
        if not kept[field]:
            kept[field] = new[field]


def clean_stations(rows: Iterable[Mapping[str, str]]) -> tuple[list[dict], CleanStats]:
    """Drop non-US rows and collapse to one station per OPIS ID.

    Keeps the lowest price per ID and the first non-empty name/address/city/state.
    Returns the cleaned station dicts (in first-seen order) and stage counts.
    """
    stats = CleanStats()
    by_id: dict[int, dict] = {}
    for raw in rows:
        stats.rows_read += 1
        parsed = _parse_row(raw)
        if parsed is None:
            stats.dropped_invalid += 1
        elif parsed["state"] not in US_STATES:
            stats.dropped_non_us += 1
        elif parsed["opis_id"] in by_id:
            _merge(by_id[parsed["opis_id"]], parsed)
        else:
            by_id[parsed["opis_id"]] = parsed
    stats.after_dedupe = len(by_id)
    return list(by_id.values()), stats
