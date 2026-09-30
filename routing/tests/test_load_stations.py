import pytest
from django.core.management import call_command

from routing.models import FuelStation

CSV = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
1,ONE,I-1,tomah,wi,1,3.50
1,ONE DUP,I-1,Tomah,WI,1,3.10
2,CANADA,Hwy 1,Toronto,ON,1,3.00
"""


@pytest.mark.django_db
def test_load_is_idempotent(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text(CSV)
    for _ in range(2):
        call_command("load_stations", csv=f)
    assert FuelStation.objects.count() == 1
    station = FuelStation.objects.get()
    assert (station.city, station.state, str(station.price)) == ("Tomah", "WI", "3.1000")


@pytest.mark.django_db
def test_reload_keeps_coordinates_and_updates_price(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text(CSV)
    call_command("load_stations", csv=f)
    FuelStation.objects.filter(opis_id=1).update(lat=44.0, lng=-90.5)

    f.write_text(CSV.replace("3.10", "2.90"))
    call_command("load_stations", csv=f)

    station = FuelStation.objects.get(opis_id=1)
    assert (station.lat, station.lng) == (44.0, -90.5)
    assert str(station.price) == "2.9000"


@pytest.mark.django_db
def test_reload_deletes_stations_missing_from_csv(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text(CSV + "9,OTHER,I-2,Reno,NV,1,3.40\n")
    call_command("load_stations", csv=f)
    assert FuelStation.objects.count() == 2

    f.write_text(CSV)
    call_command("load_stations", csv=f)
    assert list(FuelStation.objects.values_list("opis_id", flat=True)) == [1]
