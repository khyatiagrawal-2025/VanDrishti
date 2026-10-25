from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("hotspots/", views.hotspots_cases, name="hotspots"),
    path("api/refresh/", views.api_refresh_telemetry, name="api_refresh"),
    path("api/observations/", views.api_observations, name="api_observations"),
    path("api/observations/<str:observation_id>/", views.api_observation_detail, name="api_observation_detail"),
    path("api/cases/", views.api_cases, name="api_cases"),
    path("api/cases/<str:case_id>/", views.api_case_detail, name="api_case_detail"),
]
