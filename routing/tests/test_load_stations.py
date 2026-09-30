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
