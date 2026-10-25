from django.db import models


class Observation(models.Model):
    PRIORITY_CHOICES = [
        ("high", "High"),
        ("medium", "Medium"),
        ("low", "Low"),
    ]

    STATUS_CHOICES = [
        ("new", "New"),
        ("under-review", "Under Review"),
        ("verified", "Verified"),
        ("closed", "Closed"),
    ]

    observation_id = models.CharField(max_length=32, unique=True, db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    location_name = models.CharField(max_length=150)
    forest_range = models.CharField(max_length=100, default="Nainital Range")
    detected_at = models.DateTimeField()
    satellite_source = models.CharField(max_length=60, default="VIIRS (Suomi-NPP)")
    confidence = models.IntegerField(null=True, blank=True, help_text="Confidence percentage (0-100)")
    brightness_temp_k = models.FloatField(null=True, blank=True, help_text="Brightness Temperature (Kelvin)")
    frp_mw = models.FloatField(null=True, blank=True, help_text="Fire Radiative Power (MW)")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="medium")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-detected_at"]
        verbose_name = "Satellite Observation"
        verbose_name_plural = "Satellite Observations"

    def __str__(self):
        return f"{self.observation_id} - {self.location_name} ({self.priority.upper()})"


class TelemetrySync(models.Model):
    STATUS_CHOICES = [
        ("connected", "Connected / Operational"),
        ("disconnected", "Disconnected"),
        ("syncing", "Sync in Progress"),
        ("error", "Error"),
    ]

    source_name = models.CharField(max_length=100, default="NASA FIRMS (VIIRS / MODIS)")
    division_name = models.CharField(max_length=100, default="Nainital Forest Division")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="disconnected")
    last_sync_time = models.DateTimeField(null=True, blank=True)
    observations_ingested = models.IntegerField(default=0)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Telemetry Sync Status"
        verbose_name_plural = "Telemetry Sync Statuses"

    def __str__(self):
        return f"{self.source_name} - {self.status}"
