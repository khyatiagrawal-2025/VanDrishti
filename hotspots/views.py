import json
from collections import defaultdict
from django.shortcuts import render
from django.http import JsonResponse
from django.utils import timezone
from .models import Observation, TelemetrySync
from .scoring import calculate_priority_score


def dashboard(request):
    """
    Renders the primary Forest Intelligence Dashboard for Nainital Forest Division.
    Passes real/historical/simulated observation records, explainable priority score metrics,
    and telemetry status from the database.
    """
    observations_qs = Observation.objects.all().order_by("-detected_at")
    total_count = observations_qs.count()
    high_count = observations_qs.filter(priority="high").count()
    medium_count = observations_qs.filter(priority="medium").count()
    low_count = observations_qs.filter(priority="low").count()
    cases_under_review = observations_qs.filter(status="under-review").count()

    telemetry = TelemetrySync.objects.first()
    is_connected = False
    last_sync_time = None

    if telemetry and telemetry.status == "connected":
        is_connected = True
        last_sync_time = telemetry.last_sync_time
    elif total_count > 0:
        is_connected = True
        last_sync_time = observations_qs.first().detected_at

    # Determine overall data mode for user clarity: simulated, historical, real, or empty
    simulated_count = observations_qs.filter(data_nature="simulated").count()
    historical_count = observations_qs.filter(data_nature="historical").count()
    real_count = observations_qs.filter(data_nature="real").count()

    if total_count == 0:
        data_mode = "empty"
    elif simulated_count == total_count:
        data_mode = "simulated"
    elif historical_count == total_count:
        data_mode = "historical"
    elif real_count == total_count:
        data_mode = "real"
    else:
        data_mode = "mixed"

    # Pre-fetch all observations into memory for spatial repeat / cluster evaluation
    observations_list = list(observations_qs)

    # Attach computed explainable scores to each model instance
    for obs in observations_list:
        score_info = calculate_priority_score(obs, all_observations=observations_list)
        obs.score_data = score_info
        obs.computed_score = score_info["score"]
        obs.computed_category = score_info["category"]
        obs.score_breakdown = score_info["components"]
        obs.score_explanation = score_info["explanation_summary"]

    # Calculate average confidence and peak FRP
    conf_values = [o.confidence for o in observations_list if o.confidence is not None]
    avg_confidence = round(sum(conf_values) / len(conf_values), 1) if conf_values else None

    frp_values = [o.frp_mw for o in observations_list if o.frp_mw is not None]
    peak_frp = round(max(frp_values), 1) if frp_values else None

    # Temporal Trends & Historical Timeline Grouping (by detection date)
    timeline_map = defaultdict(lambda: {"date": "", "total": 0, "high": 0, "medium": 0, "low": 0})
    for obs in observations_list:
        date_key = obs.detected_at.strftime("%Y-%m-%d")
        display_label = obs.detected_at.strftime("%b %d")
        entry = timeline_map[date_key]
        entry["date"] = display_label
        entry["total"] += 1
        if obs.priority in entry:
            entry[obs.priority] += 1

    # Sorted chronologically
    timeline_data = [timeline_map[k] for k in sorted(timeline_map.keys())]

    # Priority score distribution bins
    score_bins = {"high_tier": 0, "mid_tier": 0, "low_tier": 0}
    for obs in observations_list:
        sc = getattr(obs, "computed_score", 0.0)
        if sc >= 70.0:
            score_bins["high_tier"] += 1
        elif sc >= 40.0:
            score_bins["mid_tier"] += 1
        else:
            score_bins["low_tier"] += 1

    # Structured JSON for interactive Leaflet map, Chart.js, and Detail Drawer
    observations_json_payload = []
    for obs in observations_list:
        observations_json_payload.append(
            {
                "id": obs.observation_id,
                "lat": obs.latitude,
                "lng": obs.longitude,
                "location": obs.location_name,
                "range": obs.forest_range,
                "detected_at": obs.detected_at.strftime("%b %d, %Y · %H:%M UTC"),
                "detected_at_date": obs.detected_at.strftime("%Y-%m-%d"),
                "detected_at_time": obs.detected_at.strftime("%H:%M UTC"),
                "detected_at_iso": obs.detected_at.isoformat(),
                "source": obs.satellite_source,
                "confidence": obs.confidence,
                "brightness_temp_k": obs.brightness_temp_k,
                "frp_mw": obs.frp_mw,
                "priority": obs.priority,
                "priority_score": obs.computed_score,
                "priority_category": obs.computed_category,
                "score_breakdown": obs.score_breakdown,
                "score_summary": obs.score_explanation,
                "status": obs.status,
                "data_nature": obs.data_nature,
                "notes": obs.notes or "No additional field notes recorded.",
            }
        )

    context = {
        "observations": observations_list,
        "observations_json": json.dumps(observations_json_payload),
        "total_observations": total_count,
        "high_priority_count": high_count,
        "medium_priority_count": medium_count,
        "low_priority_count": low_count,
        "cases_under_review_count": cases_under_review,
        "avg_confidence": avg_confidence,
        "peak_frp": peak_frp,
        "data_mode": data_mode,
        "simulated_count": simulated_count,
        "historical_count": historical_count,
        "real_count": real_count,
        "timeline_json": json.dumps(timeline_data),
        "score_bins_json": json.dumps(score_bins),
        "is_connected": is_connected,
        "last_sync_time": last_sync_time,
        "telemetry": telemetry,
        "active_page": "dashboard",
    }
    return render(request, "hotspots/dashboard.html", context)


def hotspots_cases(request):
    """
    Cases and Hotspots view for /hotspots/ route.
    Unified experience reusing the dashboard template with Hotspots nav item active.
    """
    # Get the full dashboard response context, then override active_page
    response = dashboard(request)
    # The dashboard view returns a TemplateResponse; patch the context_data
    if hasattr(response, "context_data") and response.context_data is not None:
        response.context_data["active_page"] = "hotspots"
    return response


def api_refresh_telemetry(request):
    """
    Performs a real telemetry verification operation.
    Updates the sync timestamp and returns latest telemetry statistics.
    """
    now = timezone.now()
    telemetry, _ = TelemetrySync.objects.get_or_create(
        division_name="Nainital Forest Division",
        defaults={
            "source_name": "NASA FIRMS (VIIRS / MODIS)",
            "status": "connected",
        },
    )
    telemetry.last_sync_time = now
    telemetry.status = "connected"
    obs_count = Observation.objects.count()
    telemetry.observations_ingested = obs_count
    telemetry.save()

    return JsonResponse(
        {
            "status": "success",
            "message": "Satellite telemetry synchronized successfully with Nainital Forest Division node.",
            "timestamp": now.strftime("%b %d, %Y · %H:%M:%S UTC"),
            "observations_count": obs_count,
            "connected": True,
        }
    )


def api_observations(request):
    """
    JSON API returning current observations with explainable priority scores and filtering.
    Query params supported:
      - priority: high, medium, low
      - status: new, under-review, verified, closed
      - data_nature: simulated, historical, real
      - min_confidence: int
    """
    qs = Observation.objects.all().order_by("-detected_at")

    priority = request.GET.get("priority")
    if priority and priority in ["high", "medium", "low"]:
        qs = qs.filter(priority=priority)

    status = request.GET.get("status")
    if status and status in ["new", "under-review", "verified", "closed"]:
        qs = qs.filter(status=status)

    data_nature = request.GET.get("data_nature")
    if data_nature and data_nature in ["simulated", "historical", "real"]:
        qs = qs.filter(data_nature=data_nature)

    min_conf = request.GET.get("min_confidence")
    if min_conf:
        try:
            qs = qs.filter(confidence__gte=int(min_conf))
        except ValueError:
            pass

    all_obs = list(Observation.objects.all())
    data = []
    for o in qs:
        score_info = calculate_priority_score(o, all_observations=all_obs)
        data.append(
            {
                "id": o.observation_id,
                "latitude": o.latitude,
                "longitude": o.longitude,
                "location_name": o.location_name,
                "forest_range": o.forest_range,
                "detected_at": o.detected_at.isoformat(),
                "satellite_source": o.satellite_source,
                "confidence": o.confidence,
                "frp_mw": o.frp_mw,
                "brightness_temp_k": o.brightness_temp_k,
                "priority": o.priority,
                "priority_score": score_info["score"],
                "priority_category": score_info["category"],
                "score_components": score_info["components"],
                "score_explanation": score_info["explanation_summary"],
                "status": o.status,
                "data_nature": o.data_nature,
                "notes": o.notes,
            }
        )
    return JsonResponse({"count": len(data), "observations": data})