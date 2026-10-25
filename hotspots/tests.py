import os
import json
import socket
import tempfile
import urllib.error
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone as dt_timezone
from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse
from django.core.management import call_command
from .models import Observation, ResponseCase, TelemetrySync
from .scoring import (
    calculate_priority_score,
    haversine_distance_km,
    in_nainital_bounds,
    NAINITAL_BOUNDS,
)
from .data_pipeline import (
    validate_observation_record,
    parse_firms_datetime,
    normalize_confidence,
    normalize_data_nature,
    normalize_status,
    import_observations_from_csv,
    fetch_firms_live_data,
    acquire_firms_hotspots,
)
from .acquisition import (
    acquire_and_ingest_firms_hotspots,
    get_firms_api_key,
    build_firms_area_url,
    map_firms_satellite_name,
    estimate_nainital_location,
    fetch_firms_csv,
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

    def test_in_nainital_bounds_helper(self):
        # Valid coordinates inside Nainital division
        self.assertTrue(in_nainital_bounds(29.3803, 79.4636))
        # Exact boundary limits (inclusive)
        self.assertTrue(in_nainital_bounds(NAINITAL_BOUNDS["min_lat"], NAINITAL_BOUNDS["min_lng"]))
        self.assertTrue(in_nainital_bounds(NAINITAL_BOUNDS["max_lat"], NAINITAL_BOUNDS["max_lng"]))

        # Outside boundary limits
        self.assertFalse(in_nainital_bounds(29.09, 79.45))
        self.assertFalse(in_nainital_bounds(29.66, 79.45))
        self.assertFalse(in_nainital_bounds(29.35, 79.09))
        self.assertFalse(in_nainital_bounds(29.35, 79.81))
        # Coordinates outside Nainital (Bangalore & Delhi)
        self.assertFalse(in_nainital_bounds(12.9716, 77.5946))
        self.assertFalse(in_nainital_bounds(28.6139, 77.2090))

        # String representations
        self.assertTrue(in_nainital_bounds("29.3803", "79.4636"))
        self.assertFalse(in_nainital_bounds("12.9716", "77.5946"))

        # Dict objects with latitude/longitude or lat/lng
        self.assertTrue(in_nainital_bounds({"latitude": 29.3952, "longitude": 79.4418}))
        self.assertTrue(in_nainital_bounds({"lat": 29.3952, "lng": 79.4418}))
        self.assertFalse(in_nainital_bounds({"latitude": 12.9716, "longitude": 77.5946}))

        # Invalid or None inputs
        self.assertFalse(in_nainital_bounds(None, None))
        self.assertFalse(in_nainital_bounds("not_a_number", "invalid"))

    def test_priority_scoring_inside_vs_outside_boundary(self):
        now = timezone.now()
        # Inside boundary observation
        obs_inside = {
            "observation_id": "VD-IN-01",
            "latitude": 29.3952,
            "longitude": 79.4418,
            "confidence": 85,
            "frp_mw": 10.0,
            "detected_at": now,
            "location_name": "China Peak",
            "forest_range": "Nainital Range",
        }
        res_inside = calculate_priority_score(obs_inside, reference_time=now)
        self.assertEqual(res_inside["components"]["forest_overlap"]["score"], 10.0)
        self.assertEqual(res_inside["components"]["forest_overlap"]["status"], "available")
        self.assertIn("inside Nainital Forest Division", res_inside["components"]["forest_overlap"]["explanation"])

        # Outside boundary observation
        obs_outside = {
            "observation_id": "VD-OUT-01",
            "latitude": 28.6139,  # Delhi coordinates
            "longitude": 77.2090,
            "confidence": 85,
            "frp_mw": 10.0,
            "detected_at": now,
            "location_name": "Out of Bounds Anomaly",
            "forest_range": "Unknown Range",
        }
        res_outside = calculate_priority_score(obs_outside, reference_time=now)
        self.assertEqual(res_outside["components"]["forest_overlap"]["score"], 2.0)
        self.assertEqual(res_outside["components"]["forest_overlap"]["status"], "available")
        self.assertIn("outside", res_outside["components"]["forest_overlap"]["explanation"])



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

    def test_import_observations_skips_outside_boundary_records(self):
        """
        Ensures CSV records outside Nainital bounding box are safely skipped before
        priority scoring and database insertion.
        """
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
        try:
            temp_file.write(
                "observation_id,latitude,longitude,observation_time,confidence,frp,satellite_source,location_name,forest_range,status,data_nature\n"
                "VD-TEST-IN-01,29.3850,79.4610,2026-05-15T10:30:00Z,90,12.0,VIIRS,Inside Peak,Nainital Central Range,new,real\n"
                "VD-TEST-OUT-BLR,12.9716,77.5946,2026-05-15T10:30:00Z,85,10.0,VIIRS,Bangalore Urban,Urban,new,real\n"
                "VD-TEST-OUT-DEL,28.6139,77.2090,2026-05-15T10:30:00Z,80,8.0,MODIS,Delhi NCR,Plain,new,real\n"
                "VD-TEST-INVALID,invalid_lat,79.4610,2026-05-15T10:30:00Z,50,5.0,VIIRS,Invalid Coords,None,new,real\n"
            )
            temp_file.close()

            # 1. Test dry_run: 1 valid, 3 skipped (2 out-of-bounds, 1 malformed)
            dry_res = import_observations_from_csv(temp_file.name, dry_run=True, filter_bounds=True)
            self.assertTrue(dry_res["success"])
            self.assertEqual(dry_res["total_rows"], 4)
            self.assertEqual(dry_res["valid_records"], 1)
            self.assertEqual(dry_res["skipped"], 3)
            self.assertEqual(len(dry_res["errors"]), 3)

            # 2. Test actual DB ingestion
            real_res = import_observations_from_csv(temp_file.name, dry_run=False, filter_bounds=True)
            self.assertTrue(real_res["success"])
            self.assertEqual(real_res["created"], 1)
            self.assertEqual(real_res["skipped"], 3)
            self.assertTrue(Observation.objects.filter(observation_id="VD-TEST-IN-01").exists())
            self.assertFalse(Observation.objects.filter(observation_id="VD-TEST-OUT-BLR").exists())
            self.assertFalse(Observation.objects.filter(observation_id="VD-TEST-OUT-DEL").exists())
        finally:
            if os.path.exists(temp_file.name):
                os.remove(temp_file.name)



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

    def test_load_observations_command_skips_outside_boundary(self):
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
        try:
            temp_file.write(
                "observation_id,latitude,longitude,observation_time,confidence,frp,satellite_source,location_name,forest_range,status,data_nature\n"
                "VD-CMD-IN,29.3850,79.4610,2026-05-15T10:30:00Z,90,12.0,VIIRS,Command Inside,Nainital Central Range,new,real\n"
                "VD-CMD-OUT,12.9716,77.5946,2026-05-15T10:30:00Z,85,10.0,VIIRS,Command Out,Urban,new,real\n"
            )
            temp_file.close()

            # Execute load_observations management command with mixed CSV
            call_command("load_observations", csv=temp_file.name)
            self.assertTrue(Observation.objects.filter(observation_id="VD-CMD-IN").exists())
            self.assertFalse(Observation.objects.filter(observation_id="VD-CMD-OUT").exists())
        finally:
            if os.path.exists(temp_file.name):
                os.remove(temp_file.name)



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


from unittest.mock import patch, MagicMock
from io import BytesIO
from django.core.management import call_command
from hotspots.data_pipeline import fetch_firms_live_data


class FirmsApiAndTileProxyTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_firms_live_missing_key(self):
        with self.settings(FIRMS_MAP_KEY=""):
            with patch.dict(os.environ, {"FIRMS_MAP_KEY": ""}, clear=False):
                res = fetch_firms_live_data(map_key="")
                self.assertFalse(res["success"])
                self.assertEqual(res["status"], "missing_credentials")
                self.assertEqual(res["created"], 0)
                self.assertIn("FIRMS_MAP_KEY", res["error"])

    def test_firms_live_valid_csv_parsing(self):
        mock_csv = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
            "29.3803,79.4636,335.2,0.38,0.36,2026-05-18,0830,N,VIIRS,nominal,2.0NRT,295.4,14.8,D\n"
        ).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_csv
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = fetch_firms_live_data(map_key="test_dummy_key_1234567890123456", source="VIIRS_NOAA20_NRT")
            self.assertTrue(res["success"])
            self.assertEqual(res["created"], 1)
            obs = Observation.objects.get(latitude=29.3803, longitude=79.4636)
            self.assertEqual(obs.data_nature, "real")
            self.assertEqual(obs.confidence, 65)  # 'nominal' normalized to 65%
            self.assertEqual(obs.frp_mw, 14.8)
            self.assertIn("thermal anomaly", obs.notes.lower())
            self.assertIn("NASA FIRMS", obs.satellite_source)

    def test_firms_live_no_anomalies(self):
        mock_csv = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n".encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_csv
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = fetch_firms_live_data(map_key="test_dummy_key_1234567890123456")
            self.assertTrue(res["success"])
            self.assertEqual(res["status"], "no_anomalies")
            self.assertEqual(res["created"], 0)

    def test_firms_live_network_error_handled_gracefully(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection timed out")):
            res = fetch_firms_live_data(map_key="test_dummy_key_1234567890123456")
            self.assertFalse(res["success"])
            self.assertEqual(res["status"], "api_error")
            self.assertIn("Connection timed out", res["error"])

    def test_fetch_firms_management_command(self):
        # Dry-run with no key should exit cleanly without unhandled exception
        call_command("fetch_firms_hotspots", dry_run=True)
        # Sample fallback should execute and import historical sample
        call_command("fetch_firms_hotspots", dry_run=True, sample_fallback=True)

    def test_api_map_tile_proxy_valid(self):
        res = self.client.get(reverse("api_map_tile_proxy", kwargs={"z": 13, "x": 5904, "y": 3396}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "image/png")
        self.assertGreater(len(res.content), 200)

    def test_api_map_tile_proxy_invalid_coords(self):
        res = self.client.get(reverse("api_map_tile_proxy", kwargs={"z": 25, "x": 0, "y": 0}))
        self.assertEqual(res.status_code, 400)

    def test_api_refresh_telemetry_endpoint(self):
        res = self.client.post(reverse("api_refresh"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["connected"])
        self.assertIn("observations_count", data)
    def test_refresh_telemetry_missing_key_reports_key_required(self):
        with self.settings(FIRMS_MAP_KEY=""):
            with patch.dict(os.environ, {"FIRMS_MAP_KEY": ""}, clear=False):
                res = self.client.post(reverse("api_refresh"))
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertEqual(data["status"], "success")
                self.assertFalse(data["live_sync_enabled"])
                self.assertIn("API key required", data["message"])

    def test_refresh_telemetry_configured_key_triggers_live_sync(self):
        mock_csv = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
            "29.3900,79.4500,320.0,0.4,0.4,2026-05-18,0900,N,VIIRS,nominal,2.0NRT,290.0,10.0,D\n"
        ).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_csv
        mock_resp.__enter__.return_value = mock_resp

        with self.settings(FIRMS_MAP_KEY="mock_dummy_key_1234567890123456"):
            with patch.dict(os.environ, {"FIRMS_MAP_KEY": "mock_dummy_key_1234567890123456"}, clear=False):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    res = self.client.post(reverse("api_refresh"))
                    self.assertEqual(res.status_code, 200)
                    data = res.json()
                    self.assertEqual(data["status"], "success")
                    self.assertTrue(data["live_sync_enabled"])
                    self.assertIn("Live FIRMS sync complete", data["message"])

    def test_load_env_file_parser(self):
        from config.settings import _load_env_file
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".env", encoding="utf-8") as f:
            f.write("# comment line\n\nTEST_UNIT_KEY_1=parsed_val_1\nTEST_UNIT_KEY_2=\"quoted_val_2\"\n")
            temp_path = f.name
        try:
            _load_env_file(temp_path)
            self.assertEqual(os.environ.get("TEST_UNIT_KEY_1"), "parsed_val_1")
            self.assertEqual(os.environ.get("TEST_UNIT_KEY_2"), "quoted_val_2")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            os.environ.pop("TEST_UNIT_KEY_1", None)
            os.environ.pop("TEST_UNIT_KEY_2", None)


class FirmsAcquisitionTest(TestCase):
    """
    Automated test suite for NASA FIRMS satellite data acquisition, URL building,
    secure environment key handling, HTTP mocking, end-to-end pipeline ingestion,
    and case-status preservation regressions.
    """

    SAMPLE_FIRMS_VIIRS_CSV = (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
        "29.3952,79.4418,348.6,0.39,0.36,2026-05-12,1145,N,VIIRS,n,2.0NRT,295.2,14.8,D\n"
        "29.4124,79.4682,339.2,0.40,0.37,2026-05-12,1015,1,VIIRS,h,2.0NRT,290.1,11.2,D\n"
        "12.9716,77.5946,310.0,0.50,0.50,2026-05-12,1000,N,VIIRS,n,2.0NRT,290.0,5.0,D\n"
    )

    def test_get_firms_api_key_secure_resolution(self):
        # 1. Test FIRMS_MAP_KEY resolution
        with patch.dict(os.environ, {"FIRMS_MAP_KEY": "KEY_AAA_123"}, clear=True):
            self.assertEqual(get_firms_api_key(), "KEY_AAA_123")

        # 2. Test NASA_FIRMS_API_KEY fallback resolution
        with patch.dict(os.environ, {"NASA_FIRMS_API_KEY": "KEY_BBB_456"}, clear=True):
            self.assertEqual(get_firms_api_key(), "KEY_BBB_456")

        # 3. Test when neither is set
        with patch.dict(os.environ, {}, clear=True):
            with patch("hotspots.acquisition.settings", MagicMock(FIRMS_MAP_KEY=None, NASA_FIRMS_API_KEY=None)):
                self.assertIsNone(get_firms_api_key())

    def test_build_firms_area_url(self):
        # Default Nainital extent URL
        url = build_firms_area_url("TEST_KEY_123", source="VIIRS_SNPP_NRT", day_range=1)
        expected_extent = f"{NAINITAL_BOUNDS['min_lng']},{NAINITAL_BOUNDS['min_lat']},{NAINITAL_BOUNDS['max_lng']},{NAINITAL_BOUNDS['max_lat']}"
        self.assertIn("https://firms.modaps.eosdis.nasa.gov/api/area/csv/TEST_KEY_123/VIIRS_SNPP_NRT/", url)
        self.assertIn(expected_extent, url)
        self.assertTrue(url.endswith("/1"))

        # URL with date specified
        url_date = build_firms_area_url("TEST_KEY_123", day_range=2, date="2026-05-12")
        self.assertTrue(url_date.endswith("/2/2026-05-12"))

        # Day range clamping (between 1 and 10)
        url_clamped = build_firms_area_url("TEST_KEY_123", day_range=25)
        self.assertIn("/10", url_clamped)

        # Missing key raises ValueError
        with self.assertRaises(ValueError):
            build_firms_area_url("")

    def test_map_firms_satellite_name(self):
        self.assertEqual(map_firms_satellite_name("N", "VIIRS"), "VIIRS (Suomi-NPP)")
        self.assertEqual(map_firms_satellite_name("1", "VIIRS"), "VIIRS (NOAA-20)")
        self.assertEqual(map_firms_satellite_name("A", "MODIS"), "MODIS (Aqua)")
        self.assertEqual(map_firms_satellite_name("T", "MODIS"), "MODIS (Terra)")
        self.assertEqual(map_firms_satellite_name("", "VIIRS"), "NASA FIRMS (VIIRS)")

    def test_estimate_nainital_location(self):
        # China Peak Ridge coordinates (29.3952, 79.4418)
        loc, forest_range = estimate_nainital_location(29.3952, 79.4418)
        self.assertIn("China Peak", loc)
        self.assertIn("Nainital", forest_range)

        # Far away coordinates fallback
        loc_far, range_far = estimate_nainital_location(12.0, 77.0)
        self.assertEqual(loc_far, "Nainital Division Beat")
        self.assertEqual(range_far, "Nainital Forest Division")

    def test_fetch_firms_csv_missing_key(self):
        with patch("hotspots.acquisition.get_firms_api_key", return_value=None):
            res = fetch_firms_csv(api_key=None)
            self.assertFalse(res["success"])
            self.assertIn("NASA FIRMS API key is missing", res["error"])
            self.assertIsNone(res["status_code"])

    @patch("urllib.request.urlopen")
    def test_fetch_firms_csv_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = self.SAMPLE_FIRMS_VIIRS_CSV.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = fetch_firms_csv(api_key="MOCK_MAP_KEY")
        self.assertTrue(res["success"])
        self.assertEqual(res["status_code"], 200)
        self.assertIn("29.3952,79.4418", res["raw_data"])

    @patch("urllib.request.urlopen")
    def test_fetch_firms_csv_http_403_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://firms.modaps.eosdis.nasa.gov/api/area/csv/...",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=None,
        )
        res = fetch_firms_csv(api_key="INVALID_KEY")
        self.assertFalse(res["success"])
        self.assertEqual(res["status_code"], 403)
        self.assertIn("authentication failed", res["error"].lower())

    @patch("urllib.request.urlopen")
    def test_fetch_firms_csv_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError(reason=socket.timeout("timed out"))
        res = fetch_firms_csv(api_key="MOCK_KEY", timeout=5)
        self.assertFalse(res["success"])
        self.assertIn("timed out", res["error"].lower())

    @patch("urllib.request.urlopen")
    def test_fetch_firms_csv_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError(reason="Name or service not known")
        res = fetch_firms_csv(api_key="MOCK_KEY")
        self.assertFalse(res["success"])
        self.assertIn("network connection error", res["error"].lower())

    @patch("urllib.request.urlopen")
    def test_fetch_firms_csv_body_level_api_error(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b"Invalid MAP KEY"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = fetch_firms_csv(api_key="BAD_KEY")
        self.assertFalse(res["success"])
        self.assertEqual(res["status_code"], 401)
        self.assertIn("map_key is invalid", res["error"].lower())

    @patch("urllib.request.urlopen")
    def test_acquire_and_ingest_firms_hotspots_end_to_end(self, mock_urlopen):
        """
        Tests complete acquisition pipeline with mocked response:
        - 2 valid inside-boundary records should be ingested and saved
        - 1 outside-boundary record should be skipped
        - TelemetrySync should be updated to connected
        """
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = self.SAMPLE_FIRMS_VIIRS_CSV.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = acquire_and_ingest_firms_hotspots(api_key="VALID_MAP_KEY", dry_run=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["total_records"], 3)
        self.assertEqual(res["valid_records"], 2)
        self.assertEqual(res["created"], 2)
        self.assertEqual(res["skipped"], 1)

        # Verify database records were persisted with real satellite data nature
        real_obs = Observation.objects.filter(data_nature="real")
        self.assertEqual(real_obs.count(), 2)

        # Check fields of one created record
        obs1 = Observation.objects.filter(latitude=29.3952, longitude=79.4418).first()
        self.assertIsNotNone(obs1)
        self.assertEqual(obs1.confidence, 65)  # 'n' nominal confidence normalized to 65
        self.assertEqual(obs1.frp_mw, 14.8)
        self.assertEqual(obs1.data_nature, "real")
        self.assertIn("China Peak", obs1.location_name)

        # Ensure outside-boundary record (Bangalore: 12.9716, 77.5946) was NOT inserted
        self.assertFalse(Observation.objects.filter(latitude=12.9716, longitude=77.5946).exists())

        # Verify TelemetrySync updated
        telemetry = TelemetrySync.objects.first()
        self.assertIsNotNone(telemetry)
        self.assertEqual(telemetry.status, "connected")
        self.assertIsNotNone(telemetry.last_sync_time)

    @patch("urllib.request.urlopen")
    def test_acquire_and_ingest_dry_run(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = self.SAMPLE_FIRMS_VIIRS_CSV.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        count_before = Observation.objects.count()
        res = acquire_and_ingest_firms_hotspots(api_key="VALID_MAP_KEY", dry_run=True)
        self.assertTrue(res["success"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["valid_records"], 2)
        self.assertEqual(res["created"], 0)
        self.assertEqual(Observation.objects.count(), count_before)

    @patch("urllib.request.urlopen")
    def test_acquire_and_ingest_empty_satellite_scan(self, mock_urlopen):
        empty_csv = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = empty_csv.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = acquire_and_ingest_firms_hotspots(api_key="VALID_MAP_KEY", dry_run=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["total_records"], 0)
        self.assertEqual(res["valid_records"], 0)
        self.assertEqual(res["created"], 0)

    @patch("urllib.request.urlopen")
    def test_fetch_firms_command_with_api_key(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = self.SAMPLE_FIRMS_VIIRS_CSV.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Run command with dry-run and api_key option
        call_command("fetch_firms_hotspots", api_key="CMD_TEST_KEY", dry_run=True)

    def test_fetch_firms_management_command_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("hotspots.acquisition.get_firms_api_key", return_value=None):
                # Should not raise exception, but output error message
                call_command("fetch_firms_hotspots")

    @patch("urllib.request.urlopen")
    def test_resync_preserves_under_review_status_and_custom_notes(self, mock_urlopen):
        """
        Regression test: When an existing observation is re-ingested from FIRMS,
        sensor metrics must update while case-management status ('under-review')
        and custom user review notes are strictly preserved.
        """
        now = timezone.now()
        obs = Observation.objects.create(
            observation_id="VD-FIRMS-20260512-939944",
            latitude=29.3952,
            longitude=79.4418,
            location_name="China Peak (Naina Peak) Ridge",
            forest_range="Nainital Central Range",
            detected_at=now - timedelta(hours=2),
            satellite_source="VIIRS (Suomi-NPP)",
            confidence=60,
            brightness_temp_k=320.0,
            frp_mw=8.0,
            priority="low",
            status="under-review",
            data_nature="real",
            notes="Assigned to Beat Guard Ramesh for line-of-sight smoke verification.",
        )

        updated_csv = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
            "29.3952,79.4418,358.2,0.39,0.36,2026-05-12,1145,N,VIIRS,h,2.0NRT,295.2,19.5,D\n"
        )
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = updated_csv.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = acquire_and_ingest_firms_hotspots(api_key="MOCK_MAP_KEY", dry_run=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["created"], 0)
        self.assertEqual(res["updated"], 1)

        obs.refresh_from_db()
        # Case-management state must be preserved
        self.assertEqual(obs.status, "under-review")
        self.assertEqual(obs.notes, "Assigned to Beat Guard Ramesh for line-of-sight smoke verification.")

        # Sensor readings and priority must be updated to latest values
        self.assertEqual(obs.confidence, 90)  # 'h' normalized to 90
        self.assertEqual(obs.frp_mw, 19.5)
        self.assertEqual(obs.brightness_temp_k, 358.2)

    @patch("urllib.request.urlopen")
    def test_resync_preserves_verified_status(self, mock_urlopen):
        """
        Regression test: When a verified observation is re-synced, status 'verified'
        and verification notes remain intact.
        """
        now = timezone.now()
        obs = Observation.objects.create(
            observation_id="VD-FIRMS-20260512-941946",
            latitude=29.4124,
            longitude=79.4682,
            location_name="Kilbury Oak Forest",
            forest_range="Kilbury Oak Forest Range",
            detected_at=now - timedelta(hours=3),
            satellite_source="VIIRS (NOAA-20)",
            confidence=70,
            brightness_temp_k=325.0,
            frp_mw=6.0,
            priority="medium",
            status="verified",
            data_nature="real",
            notes="Controlled boundary burn verified safe by Beat Officer. No spread danger.",
        )

        resync_csv = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
            "29.4124,79.4682,342.0,0.40,0.37,2026-05-12,1015,1,VIIRS,h,2.0NRT,290.1,12.5,D\n"
        )
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = resync_csv.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = acquire_and_ingest_firms_hotspots(api_key="MOCK_MAP_KEY", dry_run=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["created"], 0)
        self.assertEqual(res["updated"], 1)

        obs.refresh_from_db()
        self.assertEqual(obs.status, "verified")
        self.assertEqual(obs.notes, "Controlled boundary burn verified safe by Beat Officer. No spread danger.")
        self.assertEqual(obs.frp_mw, 12.5)

    @patch("urllib.request.urlopen")
    def test_new_observation_receives_default_new_status_and_notes(self, mock_urlopen):
        """
        Verifies that genuinely new observations receive status='new' and default
        telemetry notes generated by the pipeline.
        """
        Observation.objects.filter(observation_id="VD-FIRMS-20260512-936949").delete()

        new_csv = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
            "29.3621,79.4935,330.0,0.39,0.36,2026-05-12,0730,N,VIIRS,n,2.0NRT,290.0,7.5,D\n"
        )
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = new_csv.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = acquire_and_ingest_firms_hotspots(api_key="MOCK_MAP_KEY", dry_run=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["created"], 1)

        new_obs = Observation.objects.get(observation_id="VD-FIRMS-20260512-936949")
        self.assertEqual(new_obs.status, "new")
        self.assertIn("Live satellite telemetry ingested via NASA FIRMS", new_obs.notes)

    @patch("urllib.request.urlopen")
    def test_fetch_firms_live_data_resync_preserves_status_and_notes(self, mock_urlopen):
        """
        Regression test: Verify that fetch_firms_live_data (the caller-facing function)
        also preserves under-review and verified observation status and custom notes
        upon re-syncing while updating sensor telemetry.
        """
        now = timezone.now()
        # 1. Under-review observation preservation
        Observation.objects.filter(observation_id="VD-FIRMS-20260512-939944").delete()
        obs_review = Observation.objects.create(
            observation_id="VD-FIRMS-20260512-939944",
            latitude=29.3952,
            longitude=79.4418,
            location_name="China Peak (Naina Peak) Ridge",
            forest_range="Nainital Central Range",
            detected_at=now - timedelta(hours=2),
            satellite_source="VIIRS (Suomi-NPP)",
            confidence=60,
            brightness_temp_k=320.0,
            frp_mw=8.0,
            priority="low",
            status="under-review",
            data_nature="real",
            notes="Assigned to Field Unit 4 for ground inspection.",
        )

        # 2. Verified observation preservation
        Observation.objects.filter(observation_id="VD-FIRMS-20260512-941946").delete()
        obs_verified = Observation.objects.create(
            observation_id="VD-FIRMS-20260512-941946",
            latitude=29.4124,
            longitude=79.4682,
            location_name="Kilbury Oak Forest",
            forest_range="Kilbury Oak Forest Range",
            detected_at=now - timedelta(hours=3),
            satellite_source="VIIRS (NOAA-20)",
            confidence=70,
            brightness_temp_k=325.0,
            frp_mw=6.0,
            priority="medium",
            status="verified",
            data_nature="real",
            notes="Verified safe controlled burn by Ranger Singh.",
        )

        resync_csv = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
            "29.3952,79.4418,358.2,0.39,0.36,2026-05-12,1145,N,VIIRS,h,2.0NRT,295.2,19.5,D\n"
            "29.4124,79.4682,345.0,0.40,0.37,2026-05-12,1015,1,VIIRS,h,2.0NRT,290.1,15.0,D\n"
        )
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = resync_csv.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = fetch_firms_live_data(map_key="LIVE_KEY_TEST", dry_run=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["created"], 0)
        self.assertEqual(res["updated"], 2)

        obs_review.refresh_from_db()
        self.assertEqual(obs_review.status, "under-review")
        self.assertEqual(obs_review.notes, "Assigned to Field Unit 4 for ground inspection.")
        self.assertEqual(obs_review.frp_mw, 19.5)
        self.assertEqual(obs_review.confidence, 90)

        obs_verified.refresh_from_db()
        self.assertEqual(obs_verified.status, "verified")
        self.assertEqual(obs_verified.notes, "Verified safe controlled burn by Ranger Singh.")
        self.assertEqual(obs_verified.frp_mw, 15.0)
        self.assertEqual(obs_verified.confidence, 90)

    @patch("urllib.request.urlopen")
    def test_fetch_firms_live_data_handles_api_key_in_kwargs(self, mock_urlopen):
        """
        Fix 1 verification: Ensures fetch_firms_live_data safely resolves api_key passed
        via kwargs without raising a TypeError for duplicate keyword arguments.
        """
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Pass api_key as keyword argument (instead of map_key)
        res = fetch_firms_live_data(api_key="KWARG_KEY_123456", dry_run=True)
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "no_anomalies")

    @patch("urllib.request.urlopen")
    def test_fetch_firms_detects_http200_error_response(self, mock_urlopen):
        """
        Fix 2 verification: Detects HTTP 200 responses containing error text rather than a CSV header,
        returning clear failure instead of false no_anomalies.
        """
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b"Bad Request: The requested date range is invalid."
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # 1. Direct fetch_firms_csv check
        fetch_res = fetch_firms_csv(api_key="TEST_KEY", source="VIIRS_SNPP_NRT")
        self.assertFalse(fetch_res["success"])
        self.assertEqual(fetch_res["status"], "api_error_response")
        self.assertIn("Bad Request", fetch_res["error"])

        # 2. End-to-end acquire_and_ingest_firms_hotspots check
        ingest_res = acquire_and_ingest_firms_hotspots(api_key="TEST_KEY", source="VIIRS_SNPP_NRT")
        self.assertFalse(ingest_res["success"])
        self.assertEqual(ingest_res["status"], "api_error_response")
        self.assertIn("Bad Request", ingest_res["error"])
        self.assertEqual(ingest_res["created"], 0)

    def test_invalid_or_none_day_range_defaults_to_one(self):
        """
        Fix 3 verification: Handles None, non-integer, or negative day_range values safely,
        defaulting to 1 day.
        """
        # None day_range
        url_none = build_firms_area_url("TEST_MAP_KEY_1234567890123456", day_range=None)
        self.assertTrue(url_none.endswith("/1"))

        # Invalid string day_range
        url_invalid = build_firms_area_url("TEST_MAP_KEY_1234567890123456", day_range="not-a-number")
        self.assertTrue(url_invalid.endswith("/1"))

        # Negative day_range clamped to 1
        url_neg = build_firms_area_url("TEST_MAP_KEY_1234567890123456", day_range=-5)
        self.assertTrue(url_neg.endswith("/1"))
