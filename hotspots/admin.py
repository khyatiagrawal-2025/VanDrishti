from django.contrib import admin
from .models import Observation, ResponseCase, TelemetrySync


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = (
        "observation_id",
        "location_name",
        "forest_range",
        "priority",
        "status",
        "data_nature",
        "detected_at",
        "satellite_source",
        "confidence",
    )
    list_filter = ("priority", "status", "data_nature", "satellite_source", "forest_range")
    search_fields = ("observation_id", "location_name", "forest_range", "notes")
    ordering = ("-detected_at",)


@admin.register(ResponseCase)
class ResponseCaseAdmin(admin.ModelAdmin):
    list_display = (
        "case_id",
        "location_name",
        "forest_range",
        "priority",
        "status",
        "assigned_team",
        "observation",
        "created_at",
    )
    list_filter = ("status", "priority", "forest_range")
    search_fields = ("case_id", "location_name", "forest_range", "assigned_team", "notes")
    ordering = ("-created_at",)


@admin.register(TelemetrySync)
class TelemetrySyncAdmin(admin.ModelAdmin):
    list_display = ("source_name", "division_name", "status", "last_sync_time", "observations_ingested")
