from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("route/", views.RouteView.as_view(), name="route"),
]
