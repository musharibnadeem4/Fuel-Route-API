from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """POST /api/route/ body: "City, ST" or "lat,lng" for each end."""

    start = serializers.CharField(max_length=100)
    finish = serializers.CharField(max_length=100)
