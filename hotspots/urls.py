from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("hotspots/", views.hotspots_cases, name="hotspots"),
    path("api/refresh/", views.api_refresh_telemetry, name="api_refresh"),
    path("api/observations/", views.api_observations, name="api_observations"),
]