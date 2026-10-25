from django.contrib import admin
from .models import Observation, TelemetrySync


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = (
        "observation_id",
        "location_name",
        "forest_range",
        "priority",
        "status",
        "detected_at",
        "satellite_source",
        "confidence",
    )
    list_filter = ("priority", "status", "satellite_source", "forest_range")
    search_fields = ("observation_id", "location_name", "forest_range", "notes")
    ordering = ("-detected_at",)


@admin.register(TelemetrySync)
class TelemetrySyncAdmin(admin.ModelAdmin):
    list_display = ("source_name", "division_name", "status", "last_sync_time", "observations_ingested")
