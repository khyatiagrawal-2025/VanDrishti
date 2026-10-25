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

    DATA_NATURE_CHOICES = [
        ("simulated", "Simulated Benchmark"),
        ("historical", "Historical Record"),
        ("real", "Real Satellite Observation"),
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
    data_nature = models.CharField(
        max_length=20,
        choices=DATA_NATURE_CHOICES,
        default="simulated",
        help_text="Distinguishes real FIRMS observations, historical archives, and simulated benchmarks",
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-detected_at"]
        verbose_name = "Satellite Observation"
        verbose_name_plural = "Satellite Observations"
        indexes = [
            models.Index(fields=["-detected_at"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["status"]),
            models.Index(fields=["data_nature"]),
        ]

    def __str__(self):
        return f"{self.observation_id} - {self.location_name} ({self.priority.upper()})"

    @property
    def score_details(self):
        if not hasattr(self, "_cached_score_details"):
            from .scoring import calculate_priority_score
            self._cached_score_details = calculate_priority_score(self)
        return self._cached_score_details

    @property
    def priority_score(self):
        return self.score_details["score"]


class ResponseCase(models.Model):
    """
    Response Case tracking field verification, watchtower checks, and operational review
    for potential forest-fire hotspots in the Nainital Forest Division.
    Can be associated with a specific satellite Observation or a general forest compartment location.
    """
    STATUS_CHOICES = [
        ("new", "New"),
        ("assigned", "Assigned"),
        ("under-review", "Under Review"),
        ("verified", "Verified"),
        ("closed", "Closed"),
    ]

    PRIORITY_CHOICES = [
        ("high", "High"),
        ("medium", "Medium"),
        ("low", "Low"),
    ]

    case_id = models.CharField(max_length=32, unique=True, db_index=True)
    observation = models.ForeignKey(
        Observation,
        on_delete=models.SET_NULL,
        related_name="response_cases",
        null=True,
        blank=True,
        help_text="Associated satellite observation if generated from a detected thermal anomaly",
    )
    location_name = models.CharField(max_length=150)
    forest_range = models.CharField(max_length=100, default="Nainital Range")
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="medium")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    assigned_team = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Beat squad, forest guard, or watchtower team assigned for field verification",
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Response Case"
        verbose_name_plural = "Response Cases"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.case_id} - {self.location_name} [{self.get_status_display()}]"


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
