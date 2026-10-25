from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse
from datetime import timedelta
import json
from .models import Observation, TelemetrySync


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
            notes="Test pine forest anomaly",
        )

    def test_observation_str(self):
        self.assertIn("VD-TEST-001", str(self.obs))
        self.assertIn("Nainital Ridge", str(self.obs))
        self.assertIn("HIGH", str(self.obs))

    def test_observation_defaults(self):
        self.assertEqual(self.obs.priority, "high")
        self.assertEqual(self.obs.status, "new")


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

    def test_dashboard_with_observations(self):
        """Dashboard renders accurate counts and rows with real database records."""
        now = timezone.now()
        Observation.objects.create(
            observation_id="VD-NNT-01",
            latitude=29.39,
            longitude=79.45,
            location_name="China Peak",
            detected_at=now,
            priority="high",
            status="under-review",
        )
        Observation.objects.create(
            observation_id="VD-NNT-02",
            latitude=29.37,
            longitude=79.48,
            location_name="Bhowali Hill",
            detected_at=now - timedelta(hours=1),
            priority="medium",
            status="new",
        )

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_observations"], 2)
        self.assertEqual(response.context["high_priority_count"], 1)
        self.assertEqual(response.context["medium_priority_count"], 1)
        self.assertEqual(response.context["cases_under_review_count"], 1)
        self.assertContains(response, "VD-NNT-01")
        self.assertContains(response, "China Peak")
        self.assertContains(response, "VD-NNT-02")
        self.assertContains(response, "Bhowali Hill")


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

    def test_api_observations(self):
        Observation.objects.create(
            observation_id="VD-API-01",
            latitude=29.38,
            longitude=79.46,
            location_name="Sukhatal",
            detected_at=timezone.now(),
            priority="low",
            status="verified",
        )
        response = self.client.get(reverse("api_observations"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["observations"][0]["id"], "VD-API-01")
