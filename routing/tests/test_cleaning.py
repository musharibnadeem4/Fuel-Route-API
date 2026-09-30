from decimal import Decimal

from routing.services.cleaning import clean_stations

COLS = ["OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price"]


def row(id_, name="A", addr="I-1", city="Tomah", state="WI", price="3.0"):
    return dict(zip(COLS, [id_, name, addr, city, state, "1", price]))


def test_duplicates_keep_lowest_price_and_first_name():
    rows = [row("20", "PILOT #1243", price="3.9"), row("20", "PILOT TRAVEL CENTER", price="3.5")]
    stations, stats = clean_stations(rows)
    assert len(stations) == 1
    assert stations[0]["price"] == Decimal("3.5000")
    assert stations[0]["name"] == "PILOT #1243"
    assert (stats.rows_read, stats.after_dedupe) == (2, 1)


def test_empty_fields_are_backfilled_from_later_duplicates():
    stations, _ = clean_stations([row("5", name="", addr=""), row("5", name="Late", addr="I-9")])
    assert stations[0]["name"] == "Late"
    assert stations[0]["address"] == "I-9"


def test_canadian_rows_are_dropped():
    stations, stats = clean_stations([row("1", state="ON"), row("2", state="BC"), row("3")])
    assert [s["opis_id"] for s in stations] == [3]
    assert stats.dropped_non_us == 2


def test_dc_is_kept():
    stations, _ = clean_stations([row("1", state="DC")])
    assert len(stations) == 1


def test_whitespace_and_case_are_normalized():
    stations, _ = clean_stations([row(" 7 ", name="  Shop ", city="  big cabin ", state=" ok ", price=" 3.00733333 ")])
    s = stations[0]
    assert (s["opis_id"], s["name"], s["city"], s["state"]) == (7, "Shop", "Big Cabin", "OK")
    assert s["price"] == Decimal("3.0073")


def test_unparseable_rows_are_counted_and_skipped():
    stations, stats = clean_stations([row("x"), row("4", price=""), row("6")])
    assert len(stations) == 1
    assert stats.dropped_invalid == 2
