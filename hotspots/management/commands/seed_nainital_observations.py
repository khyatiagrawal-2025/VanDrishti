from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from hotspots.models import Observation, TelemetrySync


class Command(BaseCommand):
    help = "Seed realistic NASA FIRMS benchmark satellite observations for Nainital Forest Division"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear all existing observations and reset telemetry status",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            Observation.objects.all().delete()
            TelemetrySync.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("Cleared all observations and telemetry status."))
            return

        # Benchmark thermal anomalies located in Nainital Forest Division, Uttarakhand
        # Coordinates centered around Nainital: ~29.38° N, 79.45° E
        now = timezone.now()
        benchmark_observations = [
            {
                "observation_id": "VD-NNT-2026-081",
                "latitude": 29.3952,
                "longitude": 79.4418,
                "location_name": "China Peak (Naina Peak) Ridge",
                "forest_range": "Nainital Central Range",
                "detected_at": now - timedelta(minutes=42),
                "satellite_source": "VIIRS (Suomi-NPP)",
                "confidence": 92,
                "brightness_temp_k": 348.6,
                "frp_mw": 14.8,
                "priority": "high",
                "status": "new",
                "notes": "Thermal anomaly detected on steep pine-dominated slope. High slope exposure, smoke plume visibility requires ground watchtower check.",
            },
            {
                "observation_id": "VD-NNT-2026-079",
                "latitude": 29.4124,
                "longitude": 79.4682,
                "location_name": "Kilbury & Pangot Forest Belt",
                "forest_range": "Kilbury Oak Forest Range",
                "detected_at": now - timedelta(hours=2, minutes=15),
                "satellite_source": "VIIRS (NOAA-20)",
                "confidence": 88,
                "brightness_temp_k": 339.2,
                "frp_mw": 11.2,
                "priority": "high",
                "status": "under-review",
                "notes": "Dense oak canopy anomaly. Assigned to Bhowali-Kilbury beat guard for perimeter check. No rapid spread detected.",
            },
            {
                "observation_id": "VD-NNT-2026-075",
                "latitude": 29.3621,
                "longitude": 79.4935,
                "location_name": "Bhowali Sanatorium Hillside",
                "forest_range": "Bhowali Forest Range",
                "detected_at": now - timedelta(hours=4, minutes=30),
                "satellite_source": "MODIS (Aqua)",
                "confidence": 76,
                "brightness_temp_k": 326.4,
                "frp_mw": 7.5,
                "priority": "medium",
                "status": "under-review",
                "notes": "Chir pine litter burn suspicion near boundary fireline. Forest beat team notified.",
            },
            {
                "observation_id": "VD-NNT-2026-072",
                "latitude": 29.4285,
                "longitude": 79.5241,
                "location_name": "Gagar Pass Eastern Flank",
                "forest_range": "Ramgarh Forest Range",
                "detected_at": now - timedelta(hours=7, minutes=10),
                "satellite_source": "VIIRS (Suomi-NPP)",
                "confidence": 64,
                "brightness_temp_k": 319.1,
                "frp_mw": 5.2,
                "priority": "medium",
                "status": "new",
                "notes": "Moderate thermal signature on north-facing ridge. Moderate wind conditions.",
            },
            {
                "observation_id": "VD-NNT-2026-068",
                "latitude": 29.3510,
                "longitude": 79.4312,
                "location_name": "Sukhatal Valley Perimeter",
                "forest_range": "Nainital South Range",
                "detected_at": now - timedelta(hours=14, minutes=20),
                "satellite_source": "MODIS (Terra)",
                "confidence": 55,
                "brightness_temp_k": 312.8,
                "frp_mw": 3.8,
                "priority": "low",
                "status": "verified",
                "notes": "Controlled agricultural residue clearance on terraced boundary near Sukhatal. Verified safe by local beat guard.",
            },
            {
                "observation_id": "VD-NNT-2026-064",
                "latitude": 29.3789,
                "longitude": 79.5492,
                "location_name": "Mukteshwar Ridge Forest Beat",
                "forest_range": "Mukteshwar Range",
                "detected_at": now - timedelta(hours=22, minutes=45),
                "satellite_source": "VIIRS (NOAA-20)",
                "confidence": 49,
                "brightness_temp_k": 308.5,
                "frp_mw": 2.4,
                "priority": "low",
                "status": "closed",
                "notes": "Low radiance reading. Solar reflection from corrugated roof near ridge confirmed on inspection. Closed as false alarm.",
            },
            {
                "observation_id": "VD-NNT-2026-061",
                "latitude": 29.3845,
                "longitude": 79.4560,
                "location_name": "Snow View - Lariakanta Trail",
                "forest_range": "Nainital Central Range",
                "detected_at": now - timedelta(hours=28),
                "satellite_source": "VIIRS (Suomi-NPP)",
                "confidence": 84,
                "brightness_temp_k": 334.7,
                "frp_mw": 9.6,
                "priority": "high",
                "status": "closed",
                "notes": "Smoldering dry grass patch quickly doused by forest fire squad on yesterday's patrol. Post-event monitoring completed.",
            },
            {
                "observation_id": "VD-NNT-2026-058",
                "latitude": 29.3402,
                "longitude": 79.4815,
                "location_name": "Jyolikote Forest Descent",
                "forest_range": "Nainital South Range",
                "detected_at": now - timedelta(days=2, hours=3),
                "satellite_source": "MODIS (Aqua)",
                "confidence": 68,
                "brightness_temp_k": 321.0,
                "frp_mw": 6.1,
                "priority": "medium",
                "status": "closed",
                "notes": "Underbrush burn alongside motor road; extinguished by beat team. No tree canopy damaged.",
            },
        ]

        count = 0
        for obs in benchmark_observations:
            obj, created = Observation.objects.update_or_create(
                observation_id=obs["observation_id"],
                defaults=obs,
            )
            count += 1

        # Set or update TelemetrySync
        telemetry, _ = TelemetrySync.objects.get_or_create(
            division_name="Nainital Forest Division",
            defaults={
                "source_name": "NASA FIRMS (VIIRS / MODIS)",
                "status": "connected",
                "last_sync_time": now - timedelta(minutes=14),
                "observations_ingested": count,
                "notes": "Active telemetry feed from FIRMS VIIRS/MODIS satellites for Nainital Division.",
            },
        )
        telemetry.status = "connected"
        telemetry.last_sync_time = now - timedelta(minutes=14)
        telemetry.observations_ingested = count
        telemetry.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {count} benchmark satellite observations for Nainital Forest Division."
            )
        )
