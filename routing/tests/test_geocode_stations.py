import pytest
from django.core.management import call_command

from routing.management.commands import geocode_stations
from routing.models import FuelStation
from routing.services import geocode


@pytest.fixture
def places(tmp_path, monkeypatch):
    path = tmp_path / "us_places.csv"
    geocode.write_places_csv([("Reno", "NV", 39.5, -119.8), ("Las Vegas", "NV", 36.0, -115.0)], path)
    monkeypatch.setattr(geocode, "PLACES_CSV", path)
    monkeypatch.setattr(geocode_stations, "UNMATCHED_CSV", tmp_path / "unmatched.csv")
    return path


@pytest.mark.django_db
def test_matches_and_falls_back_to_state_centroid(places, tmp_path, capsys):
    FuelStation.objects.create(opis_id=1, name="A", address="", city="Reno", state="NV", price="3")
    FuelStation.objects.create(opis_id=2, name="B", address="", city="Atlantis", state="NV", price="3")

    call_command("geocode_stations")

    hit = FuelStation.objects.get(opis_id=1)
    miss = FuelStation.objects.get(opis_id=2)
    assert (hit.lat, hit.lng, hit.geo_approx) == (39.5, -119.8, False)
    assert (miss.lat, miss.lng, miss.geo_approx) == (37.75, -117.4, True)
    assert "Atlantis, NV" in capsys.readouterr().out
    assert "Atlantis,NV,1" in (tmp_path / "unmatched.csv").read_text()
