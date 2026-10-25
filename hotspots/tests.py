import os
import json
from datetime import timedelta
from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse
from django.core.management import call_command
from .models import Observation, TelemetrySync
from .scoring import calculate_priority_score, haversine_distance_km
from .data_pipeline import (
    validate_observation_record,
    parse_firms_datetime,
    normalize_confidence,
    import_observations_from_csv,
)


class ObservationModelTest(TestCase):
    def setUp(self):
        self.obs = Observation.objects.create(
            observation_id="VD-TEST-001",
            latitude=29.3803,
            longitude=79.4636,
            location_name="Nainital Ridge",
            forest_range="Nainital Range",
            detected_at=timezone.now(),
            satellite_source="VIIRS (Suomi-NPP)",
            confidence=85,
            brightness_temp_k=330.5,
            frp_mw=10.2,
            priority="high",
            status="new",
            data_nature="simulated",
            notes="Test pine forest anomaly",
        )

    def test_observation_str(self):
        self.assertIn("VD-TEST-001", str(self.obs))
        self.assertIn("Nainital Ridge", str(self.obs))
        self.assertIn("HIGH", str(self.obs))

    def test_observation_defaults(self):
        self.assertEqual(self.obs.priority, "high")
        self.assertEqual(self.obs.status, "new")
        self.assertEqual(self.obs.data_nature, "simulated")

    def test_observation_priority_score_property(self):
        score = self.obs.priority_score
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)
        details = self.obs.score_details
        self.assertIn("components", details)
        self.assertIn("explanation_summary", details)


class PriorityScoringTest(TestCase):
    def test_complete_high_priority_calculation(self):
        now = timezone.now()
        obs = {
            "observation_id": "VD-HIGH-01",
            "latitude": 29.3952,
            "longitude": 79.4418,
            "confidence": 95,
            "frp_mw": 18.5,
            "detected_at": now - timedelta(minutes=30),
            "location_name": "China Peak Ridge",
            "forest_range": "Nainital Central Range",
        }
        neighbor = {
            "observation_id": "VD-HIGH-02",
            "latitude": 29.3980,
            "longitude": 79.4450,
        }
        res = calculate_priority_score(obs, all_observations=[obs, neighbor], reference_time=now)
        self.assertGreaterEqual(res["score"], 70.0)
        self.assertEqual(res["category"], "high")
        self.assertTrue(res["is_complete"])
        self.assertIn("confidence", res["components"])
        self.assertEqual(res["components"]["confidence"]["status"], "available")
        self.assertGreaterEqual(res["components"]["confidence"]["score"], 28.0)
        self.assertIn("HIGH priority", res["explanation_summary"])

    def test_missing_confidence_handled_gracefully(self):
        now = timezone.now()
        obs = {
            "observation_id": "VD-NOCONF-01",
            "latitude": 29.3800,
            "longitude": 79.4600,
            "confidence": None,
            "frp_mw": 8.0,
            "detected_at": now,
            "location_name": "Sukhatal",
            "forest_range": "Nainital Range",
        }
        res = calculate_priority_score(obs, reference_time=now)
        self.assertEqual(res["components"]["confidence"]["status"], "unavailable")
        self.assertEqual(res["components"]["confidence"]["score"], 0.0)
        self.assertFalse(res["is_complete"])

    def test_missing_frp_handled_gracefully(self):
        now = timezone.now()
        obs = {
            "observation_id": "VD-NOFRP-01",
            "latitude": 29.3800,
            "longitude": 79.4600,
            "confidence": 80,
            "frp_mw": None,
            "detected_at": now,
        }
        res = calculate_priority_score(obs, reference_time=now)
        self.assertEqual(res["components"]["frp"]["status"], "unavailable")
        self.assertEqual(res["components"]["frp"]["score"], 0.0)

    def test_historical_recency_decay(self):
        now = timezone.now()
        obs_recent = {
            "observation_id": "VD-RECENT",
            "latitude": 29.3800,
            "longitude": 79.4600,
            "confidence": 70,
            "frp_mw": 5.0,
            "detected_at": now - timedelta(hours=1),
        }
        obs_old = {
            "observation_id": "VD-OLD",
            "latitude": 29.3800,
            "longitude": 79.4600,
            "confidence": 70,
            "frp_mw": 5.0,
            "detected_at": now - timedelta(days=5),
        }
        res_recent = calculate_priority_score(obs_recent, reference_time=now)
        res_old = calculate_priority_score(obs_old, reference_time=now)
        self.assertGreater(res_recent["components"]["recency"]["score"], res_old["components"]["recency"]["score"])

    def test_haversine_distance(self):
        # Distance between China Peak (29.3952, 79.4418) and Kilbury (29.4124, 79.4682) is ~3.2 km
        dist = haversine_distance_km(29.3952, 79.4418, 29.4124, 79.4682)
        self.assertGreater(dist, 2.5)
        self.assertLess(dist, 4.0)


class DataPipelineTest(TestCase):
    def test_validate_valid_record(self):
        raw = {
            "observation_id": "VD-VAL-01",
            "latitude": "29.3850",
            "longitude": "79.4610",
            "observation_time": "2026-05-15 10:30:00",
            "confidence": "88",
            "frp": "12.4",
            "satellite": "VIIRS",
            "location_name": "Snow View",
            "forest_range": "Nainital Central Range",
            "data_nature": "simulated",
        }
        is_valid, sanitized = validate_observation_record(raw)
        self.assertTrue(is_valid)
        self.assertEqual(sanitized["observation_id"], "VD-VAL-01")
        self.assertEqual(sanitized["confidence"], 88)
        self.assertEqual(sanitized["frp_mw"], 12.4)
        self.assertTrue(sanitized["in_nainital_bounds"])

    def test_validate_out_of_bounds_coordinates(self):
        raw = {
            "latitude": "12.9716",  # Bangalore coordinates (outside Nainital)
            "longitude": "77.5946",
            "observation_time": "2026-05-15 10:30:00",
        }
        is_valid, sanitized = validate_observation_record(raw)
        self.assertTrue(is_valid)
        self.assertFalse(sanitized["in_nainital_bounds"])

    def test_validate_malformed_coordinates(self):
        raw = {
            "latitude": "not-a-number",
            "longitude": "79.4610",
            "observation_time": "2026-05-15 10:30:00",
        }
        is_valid, err = validate_observation_record(raw)
        self.assertFalse(is_valid)
        self.assertIn("Malformed coordinates", err)

    def test_parse_firms_datetime(self):
        dt1 = parse_firms_datetime("2026-05-14", "0835")
        self.assertIsNotNone(dt1)
        self.assertEqual(dt1.hour, 8)
        self.assertEqual(dt1.minute, 35)

        dt2 = parse_firms_datetime("2026-05-14T14:20:00Z")
        self.assertIsNotNone(dt2)
        self.assertEqual(dt2.hour, 14)

    def test_normalize_confidence_firms_categories(self):
        self.assertEqual(normalize_confidence("h"), 90)
        self.assertEqual(normalize_confidence("n"), 65)
        self.assertEqual(normalize_confidence("l"), 30)
        self.assertEqual(normalize_confidence(85), 85)
        self.assertIsNone(normalize_confidence(None))

    def test_import_observations_from_csv(self):
        csv_path = os.path.join("hotspots", "data", "simulated_benchmark_hotspots.csv")
        self.assertTrue(os.path.exists(csv_path))
        res = import_observations_from_csv(csv_path, default_data_nature="simulated")
        self.assertTrue(res["success"])
        self.assertGreater(res["created"] + res["updated"], 0)
        self.assertEqual(res["skipped"], 0)


class DashboardViewTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_dashboard_empty_state(self):
        """Dashboard renders gracefully when there are zero observations."""
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "VanDrishti")
        self.assertContains(response, "Forest Monitoring &amp; Hotspot Intelligence")
        self.assertContains(response, "TOTAL OBSERVATIONS")
        self.assertEqual(response.context["total_observations"], 0)
        self.assertEqual(response.context["high_priority_count"], 0)
        self.assertEqual(response.context["cases_under_review_count"], 0)
        self.assertEqual(response.context["data_mode"], "empty")

    def test_dashboard_with_observations(self):
        """Dashboard renders accurate counts, scores, and rows with database records."""
        now = timezone.now()
        Observation.objects.create(
            observation_id="VD-NNT-01",
            latitude=29.39,
            longitude=79.45,
            location_name="China Peak",
            detected_at=now,
            priority="high",
            confidence=90,
            frp_mw=14.0,
            status="under-review",
            data_nature="simulated",
        )
        Observation.objects.create(
            observation_id="VD-NNT-02",
            latitude=29.37,
            longitude=79.48,
            location_name="Bhowali Hill",
            detected_at=now - timedelta(hours=1),
            priority="medium",
            confidence=70,
            frp_mw=6.5,
            status="new",
            data_nature="simulated",
        )

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_observations"], 2)
        self.assertEqual(response.context["high_priority_count"], 1)
        self.assertEqual(response.context["medium_priority_count"], 1)
        self.assertEqual(response.context["cases_under_review_count"], 1)
        self.assertEqual(response.context["data_mode"], "simulated")
        self.assertContains(response, "VD-NNT-01")
        self.assertContains(response, "China Peak")
        self.assertContains(response, "VD-NNT-02")
        self.assertContains(response, "Bhowali Hill")
        self.assertContains(response, "SIMULATED BENCHMARK DATASET")

        # Verify JSON injection
        json_data = json.loads(response.context["observations_json"])
        self.assertEqual(len(json_data), 2)
        self.assertIn("priority_score", json_data[0])
        self.assertIn("score_breakdown", json_data[0])


class HotspotsCasesViewTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_hotspots_route(self):
        response = self.client.get(reverse("hotspots"))
        self.assertEqual(response.status_code, 200)


class ApiEndpointsTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_api_refresh(self):
        response = self.client.get(reverse("api_refresh"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["connected"])
        self.assertIn("timestamp", data)

    def test_api_observations_and_filters(self):
        now = timezone.now()
        Observation.objects.create(
            observation_id="VD-API-01",
            latitude=29.38,
            longitude=79.46,
            location_name="Sukhatal",
            detected_at=now,
            priority="low",
            confidence=45,
            frp_mw=2.5,
            status="verified",
            data_nature="historical",
        )
        Observation.objects.create(
            observation_id="VD-API-02",
            latitude=29.40,
            longitude=79.45,
            location_name="China Peak",
            detected_at=now,
            priority="high",
            confidence=95,
            frp_mw=15.0,
            status="new",
            data_nature="real",
        )

        # 1. Unfiltered count
        res1 = self.client.get(reverse("api_observations"))
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(data1["count"], 2)

        # 2. Filter by priority
        res2 = self.client.get(reverse("api_observations") + "?priority=high")
        data2 = res2.json()
        self.assertEqual(data2["count"], 1)
        self.assertEqual(data2["observations"][0]["id"], "VD-API-02")
        self.assertIn("priority_score", data2["observations"][0])
        self.assertIn("score_components", data2["observations"][0])

        # 3. Filter by min_confidence
        res3 = self.client.get(reverse("api_observations") + "?min_confidence=80")
        data3 = res3.json()
        self.assertEqual(data3["count"], 1)
        self.assertEqual(data3["observations"][0]["id"], "VD-API-02")


class ManagementCommandsTest(TestCase):
    def test_seed_nainital_observations_command(self):
        call_command("seed_nainital_observations")
        self.assertGreaterEqual(Observation.objects.count(), 8)
        self.assertEqual(TelemetrySync.objects.count(), 1)

    def test_load_observations_dry_run_command(self):
        csv_file = os.path.join("hotspots", "data", "historical_firms_nainital_sample.csv")
        call_command("load_observations", csv=csv_file, dry_run=True)
