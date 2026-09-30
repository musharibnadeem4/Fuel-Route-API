from django.db import models


class FuelStation(models.Model):
    """A truck stop with its retail diesel price and (once geocoded) coordinates."""

    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price = models.DecimalField(max_digits=6, decimal_places=4)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    geo_approx = models.BooleanField(default=False)  # True: state-centroid fallback

    class Meta:
        indexes = [
            models.Index(fields=["lat", "lng"], name="station_lat_lng_idx"),
            models.Index(fields=["state", "city"], name="station_state_city_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
