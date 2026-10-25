import os
import json
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse
from django.core.management import call_command
from .models import Observation, ResponseCase, TelemetrySync
from .scoring import calculate_priority_score, haversine_distance_km, NAINITAL_BOUNDS
from .data_pipeline import (
    validate_observation_record,
    parse_firms_datetime,
    normalize_confidence,
    normalize_data_nature,
    normalize_status,
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

    def test_dashboard_basemap_elements(self):
        """Verify Leaflet mount point, basemap switcher buttons, and tile error banner exist."""
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="dashboardMap"')
        self.assertContains(response, 'id="btnLayerOSM"')
        self.assertContains(response, 'id="btnLayerTopo"')
        self.assertContains(response, 'id="btnLayerVoyager"')
        self.assertContains(response, 'id="mapTileErrorBanner"')
        self.assertContains(response, 'id="mapCoordsHud"')


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


# =============================================================================
# Additional Robustness & Integration Tests for Member 1 Deliverables
# =============================================================================

class ResponseCaseModelTest(TestCase):
    def setUp(self):
        self.obs = Observation.objects.create(
            observation_id="VD-RC-01",
            latitude=29.3952,
            longitude=79.4418,
            location_name="China Peak",
            forest_range="Nainital Central Range",
            detected_at=timezone.now(),
            priority="high",
            status="under-review",
        )

    def test_create_response_case_linked_to_observation(self):
        case = ResponseCase.objects.create(
            case_id="CASE-NNT-2026-001",
            observation=self.obs,
            location_name=self.obs.location_name,
            forest_range=self.obs.forest_range,
            latitude=self.obs.latitude,
            longitude=self.obs.longitude,
            priority="high",
            status="under-review",
            assigned_team="China Peak Beat Squad",
            notes="Watchtower visual check ordered.",
        )
        self.assertEqual(case.case_id, "CASE-NNT-2026-001")
        self.assertEqual(case.observation, self.obs)
        self.assertEqual(case.assigned_team, "China Peak Beat Squad")
        self.assertIn("CASE-NNT-2026-001", str(case))
        self.assertIn("China Peak", str(case))
        self.assertIn(case, self.obs.response_cases.all())

    def test_create_standalone_response_case(self):
        case = ResponseCase.objects.create(
            case_id="CASE-STANDALONE-01",
            location_name="Mukteshwar Perimeter Fireline",
            forest_range="Mukteshwar Range",
            latitude=29.47,
            longitude=79.65,
            priority="medium",
            status="assigned",
            assigned_team="Mukteshwar Beat Guard",
        )
        self.assertIsNone(case.observation)
        self.assertEqual(case.status, "assigned")
        self.assertEqual(case.priority, "medium")


class ObservationDetailEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.obs = Observation.objects.create(
            observation_id="VD-DETAIL-01",
            latitude=29.3803,
            longitude=79.4636,
            location_name="Nainital South Beat",
            forest_range="Nainital South Range",
            detected_at=timezone.now(),
            satellite_source="VIIRS (Suomi-NPP)",
            confidence=82,
            frp_mw=9.4,
            priority="high",
            status="new",
            data_nature="historical",
        )

    def test_get_valid_observation_detail(self):
        url = reverse("api_observation_detail", kwargs={"observation_id": "VD-DETAIL-01"})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["observation"]["id"], "VD-DETAIL-01")
        self.assertIn("priority_score", data["observation"])
        self.assertIn("score_components", data["observation"])
        self.assertEqual(data["observation"]["location_name"], "Nainital South Beat")

    def test_get_nonexistent_observation_detail(self):
        url = reverse("api_observation_detail", kwargs={"observation_id": "VD-NONEXISTENT-999"})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 404)
        data = res.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("not found", data["error"])

    def test_update_observation_status_valid(self):
        url = reverse("api_observation_detail", kwargs={"observation_id": "VD-DETAIL-01"})
        payload = {"status": "under-review", "notes": "Dispatched beat guard for visual verification."}
        res = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.obs.refresh_from_db()
        self.assertEqual(self.obs.status, "under-review")
        self.assertIn("Dispatched beat guard", self.obs.notes)

    def test_update_observation_status_invalid_choice(self):
        url = reverse("api_observation_detail", kwargs={"observation_id": "VD-DETAIL-01"})
        payload = {"status": "nonexistent-status"}
        res = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("Invalid status", data["error"])


class ResponseCaseEndpointsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.obs = Observation.objects.create(
            observation_id="VD-OBS-CASE-01",
            latitude=29.3900,
            longitude=79.4500,
            location_name="Kilbury Road Flank",
            forest_range="Kilbury Oak Forest Range",
            detected_at=timezone.now(),
            priority="high",
            status="under-review",
        )

    def test_list_cases_empty(self):
        res = self.client.get(reverse("api_cases"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["cases"], [])

    def test_create_case_with_observation_link(self):
        payload = {
            "observation_id": "VD-OBS-CASE-01",
            "assigned_team": "Kilbury Sector Squad",
            "notes": "Verify smoke plume reported from watchtower.",
        }
        res = self.client.post(reverse("api_cases"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["case"]["observation_id"], "VD-OBS-CASE-01")
        self.assertEqual(data["case"]["location_name"], "Kilbury Road Flank")
        self.assertEqual(data["case"]["assigned_team"], "Kilbury Sector Squad")
        self.assertEqual(ResponseCase.objects.count(), 1)

    def test_create_case_invalid_observation_id(self):
        payload = {
            "observation_id": "VD-DOES-NOT-EXIST",
            "location_name": "Somewhere",
        }
        res = self.client.post(reverse("api_cases"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 404)
        data = res.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("not found", data["error"])

    def test_create_case_missing_location_name(self):
        payload = {
            "assigned_team": "Squad 1",
        }
        res = self.client.post(reverse("api_cases"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("location_name", data["error"])

    def test_get_case_detail_valid_and_nonexistent(self):
        case = ResponseCase.objects.create(
            case_id="CASE-TEST-100",
            location_name="Snow View Trail",
            forest_range="Nainital Central Range",
            priority="medium",
            status="assigned",
        )
        # Valid retrieval
        res_valid = self.client.get(reverse("api_case_detail", kwargs={"case_id": "CASE-TEST-100"}))
        self.assertEqual(res_valid.status_code, 200)
        self.assertEqual(res_valid.json()["case"]["case_id"], "CASE-TEST-100")

        # Nonexistent ID
        res_invalid = self.client.get(reverse("api_case_detail", kwargs={"case_id": "CASE-DOES-NOT-EXIST"}))
        self.assertEqual(res_invalid.status_code, 404)
        self.assertEqual(res_invalid.json()["status"], "error")

    def test_update_case_status_and_assignment(self):
        case = ResponseCase.objects.create(
            case_id="CASE-TEST-200",
            location_name="Bhowali Foothills",
            forest_range="Bhowali Forest Range",
            priority="low",
            status="new",
        )
        url = reverse("api_case_detail", kwargs={"case_id": "CASE-TEST-200"})
        payload = {
            "status": "verified",
            "assigned_team": "Bhowali Beat Team Alpha",
            "notes": "Controlled stubble burn verified extinguished.",
        }
        res = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 200)
        case.refresh_from_db()
        self.assertEqual(case.status, "verified")
        self.assertEqual(case.assigned_team, "Bhowali Beat Team Alpha")


class HotspotsCasesNavigationTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_dashboard_active_page_context(self):
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["active_page"], "dashboard")

    def test_hotspots_cases_active_page_context(self):
        res = self.client.get(reverse("hotspots"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["active_page"], "hotspots")


class DataPipelineExtendedTest(TestCase):
    def test_normalize_data_nature_aliases(self):
        self.assertEqual(normalize_data_nature("sample"), "simulated")
        self.assertEqual(normalize_data_nature("benchmark"), "simulated")
        self.assertEqual(normalize_data_nature("archive"), "historical")
        self.assertEqual(normalize_data_nature("latest-available"), "real")
        self.assertEqual(normalize_data_nature("live"), "real")
        self.assertEqual(normalize_data_nature("unknown_type", default="historical"), "historical")

    def test_normalize_status_aliases(self):
        self.assertEqual(normalize_status("under review"), "under-review")
        self.assertEqual(normalize_status("checking"), "under-review")
        self.assertEqual(normalize_status("resolved"), "closed")
        self.assertEqual(normalize_status("assigned"), "new")
        self.assertEqual(normalize_status("new"), "new")

    def test_filter_bounds_option_in_import(self):
        # Create a temp CSV with 1 inside Nainital bound, 1 outside (e.g. Bangalore coordinates)
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write("observation_id,latitude,longitude,observation_time,confidence,frp\n")
            f.write("VD-IN-BOUNDS,29.3800,79.4600,2026-05-15 10:00:00,85,12.0\n")
            f.write("VD-OUT-BOUNDS,12.9716,77.5946,2026-05-15 10:00:00,85,12.0\n")
            temp_path = f.name

        try:
            res = import_observations_from_csv(temp_path, filter_bounds=True)
            self.assertTrue(res["success"])
            self.assertEqual(res["created"], 1)
            self.assertEqual(res["skipped"], 1)
            self.assertTrue(Observation.objects.filter(observation_id="VD-IN-BOUNDS").exists())
            self.assertFalse(Observation.objects.filter(observation_id="VD-OUT-BOUNDS").exists())
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_import_empty_csv(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write("")  # Empty file
            temp_path = f.name

        try:
            res = import_observations_from_csv(temp_path)
            self.assertTrue(res["success"])
            self.assertEqual(res["total_rows"], 0)
            self.assertEqual(res["created"], 0)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_import_duplicate_observations(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv", encoding="utf-8") as f:
            f.write("observation_id,latitude,longitude,observation_time,confidence,frp\n")
            f.write("VD-DUP-01,29.3800,79.4600,2026-05-15 10:00:00,80,10.0\n")
            temp_path = f.name

        try:
            res1 = import_observations_from_csv(temp_path)
            self.assertEqual(res1["created"], 1)
            # Re-import same CSV
            res2 = import_observations_from_csv(temp_path)
            self.assertEqual(res2["created"], 0)
            self.assertEqual(res2["updated"], 1)
            self.assertEqual(Observation.objects.filter(observation_id="VD-DUP-01").count(), 1)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class ScoringEngineResilienceTest(TestCase):
    def test_calculate_with_missing_and_invalid_inputs(self):
        # Observation with string/invalid confidence and FRP
        obs = {
            "observation_id": "VD-RES-01",
            "latitude": "29.38",
            "longitude": "79.46",
            "confidence": "invalid-str",
            "frp_mw": "bad-frp",
            "detected_at": "invalid-date",
            "location_name": "Test Point",
        }
        res = calculate_priority_score(obs)
        self.assertIsInstance(res["score"], float)
        self.assertEqual(res["components"]["confidence"]["status"], "unavailable")
        self.assertEqual(res["components"]["frp"]["status"], "unavailable")
        self.assertEqual(res["components"]["recency"]["status"], "unavailable")
        self.assertEqual(res["category"], "low")

    def test_calculate_with_datetime_object(self):
        now = datetime(2026, 5, 20, 14, 0, tzinfo=dt_timezone.utc)
        obs = {
            "observation_id": "VD-DT-01",
            "latitude": 29.3800,
            "longitude": 79.4600,
            "confidence": 90,
            "frp_mw": 15.0,
            "detected_at": now - timedelta(hours=1),
            "location_name": "China Peak",
            "forest_range": "Nainital Central Range",
        }
        res = calculate_priority_score(obs, reference_time=now)
        self.assertEqual(res["components"]["recency"]["status"], "available")
        self.assertEqual(res["components"]["recency"]["score"], 15.0)

    def test_search_filter_in_api_observations(self):
        client = Client()
        Observation.objects.create(
            observation_id="VD-SEARCH-PINE",
            latitude=29.38,
            longitude=79.46,
            location_name="Nainital Pine Valley",
            forest_range="Nainital South Range",
            detected_at=timezone.now(),
            priority="high",
            confidence=85,
        )
        Observation.objects.create(
            observation_id="VD-SEARCH-OAK",
            latitude=29.41,
            longitude=79.47,
            location_name="Kilbury Oak Sanctuary",
            forest_range="Kilbury Range",
            detected_at=timezone.now(),
            priority="medium",
            confidence=75,
        )

        res = client.get(reverse("api_observations") + "?search=Kilbury")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["observations"][0]["id"], "VD-SEARCH-OAK")
