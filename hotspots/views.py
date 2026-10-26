import json
from collections import defaultdict
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import Observation, ResponseCase, TelemetrySync
from .scoring import calculate_priority_score


import csv


def get_surveillance_context(request, active_page="dashboard"):
    """
    Builds the unified surveillance context for all VanDrishti portal pages.
    Provides real/historical/simulated observation records, explainable priority score metrics,
    response cases, sensor breakdowns, and telemetry status from the database.
    """
    observations_qs = Observation.objects.all().order_by("-detected_at")
    observations_list = list(observations_qs)
    total_count = len(observations_list)

    # In-memory single-pass aggregation to avoid redundant database roundtrips
    high_count = 0
    medium_count = 0
    low_count = 0
    cases_under_review = 0
    simulated_count = 0
    historical_count = 0
    real_count = 0
    conf_values = []
    frp_values = []
    sensor_counts = defaultdict(int)

    for obs in observations_list:
        if obs.priority == "high":
            high_count += 1
        elif obs.priority == "medium":
            medium_count += 1
        elif obs.priority == "low":
            low_count += 1

        if obs.status == "under-review":
            cases_under_review += 1

        if obs.data_nature == "simulated":
            simulated_count += 1
        elif obs.data_nature == "historical":
            historical_count += 1
        elif obs.data_nature == "real":
            real_count += 1

        if obs.confidence is not None:
            conf_values.append(obs.confidence)
        if obs.frp_mw is not None:
            frp_values.append(obs.frp_mw)

        if obs.satellite_source:
            sensor_counts[obs.satellite_source] += 1

    # Response cases
    response_cases_list = list(ResponseCase.objects.all().order_by("-created_at"))
    active_cases_count = sum(1 for c in response_cases_list if c.status in ["new", "assigned", "under-review"])

    # Telemetry sync status
    telemetry = TelemetrySync.objects.first()
    is_connected = False
    last_sync_time = None

    if telemetry and telemetry.status == "connected":
        is_connected = True
        last_sync_time = telemetry.last_sync_time
    elif total_count > 0:
        is_connected = True
        last_sync_time = observations_list[0].detected_at

    # Determine overall data mode for user clarity: simulated, historical, real, empty, or mixed
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

    # Attach computed explainable scores to each model instance
    for obs in observations_list:
        score_info = calculate_priority_score(obs, all_observations=observations_list)
        obs.score_data = score_info
        obs.computed_score = score_info["score"]
        obs.computed_category = score_info["category"]
        obs.score_breakdown = score_info["components"]
        obs.score_explanation = score_info["explanation_summary"]

    # Calculate average confidence and peak FRP
    avg_confidence = round(sum(conf_values) / len(conf_values), 1) if conf_values else None
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

    # Distinct forest ranges in division
    distinct_ranges = sorted(list(set(o.forest_range for o in observations_list if o.forest_range)))

    return {
        "observations": observations_list,
        "recent_observations": observations_list[:6],
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
        "sensor_counts": dict(sensor_counts),
        "response_cases": response_cases_list,
        "response_cases_count": len(response_cases_list),
        "active_cases_count": active_cases_count,
        "distinct_ranges": distinct_ranges,
        "is_connected": is_connected,
        "last_sync_time": last_sync_time,
        "telemetry": telemetry,
        "active_page": active_page,
    }


def dashboard(request, active_page="dashboard"):
    """
    Page A: Overview Dashboard.
    Presents system health, real KPI summary, recent detections, interactive map preview,
    priority stratification charts, and quick operational shortcuts.
    """
    context = get_surveillance_context(request, active_page="dashboard")
    return render(request, "hotspots/dashboard.html", context)


def hotspots_cases(request):
    """
    Page B: Hotspots Explorer.
    Dedicated listing with real-time search, multi-factor filtering, severity indicators,
    tabular breakdown, and response case tracking.
    """
    context = get_surveillance_context(request, active_page="hotspots")
    return render(request, "hotspots/hotspots.html", context)


def geospatial_map(request):
    """
    Page C: Geospatial Surveillance Map.
    Full-viewport interactive Leaflet GIS workspace with Streets, Topo, and Satellite
    basemaps, priority filter chips, division polygon, and anomaly markers.
    """
    context = get_surveillance_context(request, active_page="map")
    return render(request, "hotspots/map.html", context)


def satellite_intelligence(request):
    """
    Page D: Satellite Intelligence.
    Satellite pass telemetry, spaceborne sensor indicators (VIIRS, MODIS),
    temporal trends, FRP radiance analytics, and data provenance audit.
    """
    context = get_surveillance_context(request, active_page="satellite")
    return render(request, "hotspots/satellite.html", context)


def alerts_reports(request):
    """
    Page E: Alerts & Reports.
    High-priority escalation ledger, watchtower review assignments,
    active response cases, and CSV export for field crews.
    """
    context = get_surveillance_context(request, active_page="reports")
    return render(request, "hotspots/reports.html", context)


def api_export_report(request):
    """
    Generates and downloads a clean, structured CSV report of observations
    and priority scores for forestry field personnel and division command.
    """
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"VanDrishti_Surveillance_Report_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Observation ID",
        "Detection Time (UTC)",
        "Location",
        "Forest Range",
        "Latitude",
        "Longitude",
        "Satellite Instrument",
        "Confidence (%)",
        "FRP (MW)",
        "Brightness Temp (K)",
        "Priority Tier",
        "Priority Score (0-100)",
        "Review Status",
        "Data Nature",
        "Field Notes",
    ])

    observations = list(Observation.objects.all().order_by("-detected_at"))
    for obs in observations:
        score_info = calculate_priority_score(obs, all_observations=observations)
        writer.writerow([
            obs.observation_id,
            obs.detected_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            obs.location_name,
            obs.forest_range,
            obs.latitude,
            obs.longitude,
            obs.satellite_source,
            obs.confidence if obs.confidence is not None else "",
            obs.frp_mw if obs.frp_mw is not None else "",
            obs.brightness_temp_k if obs.brightness_temp_k is not None else "",
            obs.priority.upper(),
            score_info["score"],
            obs.status,
            obs.data_nature,
            obs.notes or "",
        ])

    return response


@csrf_exempt
def api_refresh_telemetry(request):
    """
    Performs telemetry verification and Near-Real-Time (NRT) satellite observation sync.
    Reads FIRMS_MAP_KEY from Django settings or environment variables (populated from root .env).
    If key is missing, provides an explicit message indicating the API key is required.
    """
    import os
    from django.conf import settings
    from .data_pipeline import fetch_firms_live_data

    now = timezone.now()
    map_key = (
        getattr(settings, "FIRMS_MAP_KEY", None)
        or os.environ.get("FIRMS_MAP_KEY", "")
    ).strip()

    try:
        telemetry, _ = TelemetrySync.objects.get_or_create(
            division_name="Nainital Forest Division",
            defaults={
                "source_name": "NASA FIRMS (VIIRS / MODIS)",
                "status": "connected",
            },
        )

        if not map_key:
            sync_detail = (
                "Operating in local verification mode. "
                "API key required: Set FIRMS_MAP_KEY in root .env to enable live NASA FIRMS satellite observations."
            )
            live_sync_enabled = False
        else:
            live_sync_enabled = True
            firms_res = fetch_firms_live_data(map_key=map_key, day_range=1)
            if firms_res.get("success"):
                if firms_res.get("status") == "no_anomalies":
                    sync_detail = f"Live FIRMS query OK: {firms_res.get('message')}"
                else:
                    sync_detail = f"Live FIRMS sync complete ({firms_res.get('created', 0)} new, {firms_res.get('updated', 0)} updated)."
            else:
                sync_detail = f"Live FIRMS sync note: {firms_res.get('error')}"

        telemetry.last_sync_time = now
        telemetry.status = "connected"
        obs_count = Observation.objects.count()
        telemetry.observations_ingested = obs_count
        telemetry.save()

        return JsonResponse(
            {
                "status": "success",
                "message": f"Satellite telemetry synchronized successfully. {sync_detail}",
                "timestamp": now.strftime("%b %d, %Y · %H:%M:%S UTC"),
                "observations_count": obs_count,
                "connected": True,
                "live_sync_enabled": live_sync_enabled,
            }
        )
    except Exception as exc:
        return JsonResponse(
            {
                "status": "error",
                "message": f"Telemetry synchronization error: {str(exc)}",
                "connected": False,
            },
            status=500,
        )


def api_observations(request):
    """
    JSON API returning current observations with explainable priority scores and filtering.
    Query params supported:
      - priority: high, medium, low
      - status: new, under-review, verified, closed
      - data_nature: simulated, historical, real
      - min_confidence: int
      - search: string (matches observation_id, location_name, forest_range, satellite_source)
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

    search_query = request.GET.get("search")
    if search_query:
        search_query = search_query.strip()
        from django.db.models import Q
        qs = qs.filter(
            Q(observation_id__icontains=search_query)
            | Q(location_name__icontains=search_query)
            | Q(forest_range__icontains=search_query)
            | Q(satellite_source__icontains=search_query)
        )

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


@csrf_exempt
def api_observation_detail(request, observation_id):
    """
    Retrieve or update a specific satellite observation by ID.
    GET: Returns full observation detail and explainable priority score breakdown.
    PATCH/POST: Update status ('new', 'under-review', 'verified', 'closed') or notes.
    """
    try:
        obs = Observation.objects.get(observation_id=observation_id)
    except Observation.DoesNotExist:
        return JsonResponse(
            {"status": "error", "error": f"Observation '{observation_id}' not found."},
            status=404,
        )

    if request.method in ["POST", "PATCH"]:
        try:
            body = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception:
            body = request.POST.dict()

        new_status = body.get("status")
        if new_status:
            allowed_statuses = [choice[0] for choice in Observation.STATUS_CHOICES]
            if new_status not in allowed_statuses:
                return JsonResponse(
                    {
                        "status": "error",
                        "error": f"Invalid status '{new_status}'. Allowed: {', '.join(allowed_statuses)}",
                    },
                    status=400,
                )
            obs.status = new_status

        if "notes" in body:
            obs.notes = body["notes"]

        obs.save()

    all_obs = list(Observation.objects.all())
    score_info = calculate_priority_score(obs, all_observations=all_obs)

    # Associated response cases
    cases_data = [
        {
            "case_id": c.case_id,
            "status": c.status,
            "assigned_team": c.assigned_team,
            "created_at": c.created_at.isoformat(),
        }
        for c in obs.response_cases.all()
    ]

    return JsonResponse(
        {
            "status": "success",
            "observation": {
                "id": obs.observation_id,
                "latitude": obs.latitude,
                "longitude": obs.longitude,
                "location_name": obs.location_name,
                "forest_range": obs.forest_range,
                "detected_at": obs.detected_at.isoformat(),
                "satellite_source": obs.satellite_source,
                "confidence": obs.confidence,
                "frp_mw": obs.frp_mw,
                "brightness_temp_k": obs.brightness_temp_k,
                "priority": obs.priority,
                "priority_score": score_info["score"],
                "priority_category": score_info["category"],
                "score_components": score_info["components"],
                "score_explanation": score_info["explanation_summary"],
                "status": obs.status,
                "data_nature": obs.data_nature,
                "notes": obs.notes,
                "response_cases": cases_data,
            },
        }
    )


@csrf_exempt
def api_cases(request):
    """
    List or create Response Cases for operational verification in Nainital Forest Division.
    GET: List all cases, optional query filters: status, priority, observation_id.
    POST: Create a new case. Can be linked to an observation or standalone location.
    """
    if request.method == "GET":
        qs = ResponseCase.objects.all().order_by("-created_at")

        status_param = request.GET.get("status")
        if status_param and status_param in ["new", "assigned", "under-review", "verified", "closed"]:
            qs = qs.filter(status=status_param)

        priority_param = request.GET.get("priority")
        if priority_param and priority_param in ["high", "medium", "low"]:
            qs = qs.filter(priority=priority_param)

        obs_param = request.GET.get("observation_id")
        if obs_param:
            qs = qs.filter(observation__observation_id=obs_param)

        cases_list = []
        for c in qs:
            cases_list.append(
                {
                    "case_id": c.case_id,
                    "observation_id": c.observation.observation_id if c.observation else None,
                    "location_name": c.location_name,
                    "forest_range": c.forest_range,
                    "latitude": c.latitude,
                    "longitude": c.longitude,
                    "priority": c.priority,
                    "status": c.status,
                    "assigned_team": c.assigned_team,
                    "notes": c.notes,
                    "created_at": c.created_at.isoformat(),
                    "updated_at": c.updated_at.isoformat(),
                }
            )
        return JsonResponse({"count": len(cases_list), "cases": cases_list})

    elif request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception:
            body = request.POST.dict()

        obs_id = body.get("observation_id")
        observation = None
        if obs_id:
            try:
                observation = Observation.objects.get(observation_id=obs_id)
            except Observation.DoesNotExist:
                return JsonResponse(
                    {"status": "error", "error": f"Observation '{obs_id}' not found."},
                    status=404,
                )

        location_name = body.get("location_name") or (observation.location_name if observation else None)
        if not location_name or not str(location_name).strip():
            return JsonResponse(
                {"status": "error", "error": "Field 'location_name' is required to create a response case."},
                status=400,
            )

        forest_range = body.get("forest_range") or (observation.forest_range if observation else "Nainital Range")
        priority = body.get("priority") or (observation.priority if observation else "medium")
        if priority not in ["high", "medium", "low"]:
            priority = "medium"

        status_val = body.get("status") or "new"
        if status_val not in ["new", "assigned", "under-review", "verified", "closed"]:
            return JsonResponse(
                {
                    "status": "error",
                    "error": f"Invalid status '{status_val}'. Allowed: new, assigned, under-review, verified, closed",
                },
                status=400,
            )

        lat = body.get("latitude") or (observation.latitude if observation else None)
        lng = body.get("longitude") or (observation.longitude if observation else None)
        assigned_team = body.get("assigned_team") or ""
        notes = body.get("notes") or ""

        # Deterministic or specified case ID
        now = timezone.now()
        case_id = body.get("case_id")
        if not case_id:
            count = ResponseCase.objects.count() + 1
            case_id = f"CASE-NNT-{now.strftime('%Y%m%d')}-{count:03d}"

        case = ResponseCase.objects.create(
            case_id=case_id,
            observation=observation,
            location_name=location_name.strip(),
            forest_range=forest_range.strip(),
            latitude=lat,
            longitude=lng,
            priority=priority,
            status=status_val,
            assigned_team=assigned_team.strip(),
            notes=notes.strip(),
        )

        return JsonResponse(
            {
                "status": "success",
                "message": "Response case created successfully.",
                "case": {
                    "case_id": case.case_id,
                    "observation_id": case.observation.observation_id if case.observation else None,
                    "location_name": case.location_name,
                    "forest_range": case.forest_range,
                    "latitude": case.latitude,
                    "longitude": case.longitude,
                    "priority": case.priority,
                    "status": case.status,
                    "assigned_team": case.assigned_team,
                    "notes": case.notes,
                    "created_at": case.created_at.isoformat(),
                },
            },
            status=201,
        )

    return HttpResponse(status=405)


@csrf_exempt
def api_case_detail(request, case_id):
    """
    Retrieve or update a specific Response Case by ID.
    GET: Returns response case detail.
    PATCH/POST: Update status, assigned team, priority, or field notes.
    """
    try:
        case = ResponseCase.objects.get(case_id=case_id)
    except ResponseCase.DoesNotExist:
        return JsonResponse(
            {"status": "error", "error": f"Response case '{case_id}' not found."},
            status=404,
        )

    if request.method in ["POST", "PATCH"]:
        try:
            body = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception:
            body = request.POST.dict()

        if "status" in body:
            new_status = body["status"]
            allowed_statuses = [choice[0] for choice in ResponseCase.STATUS_CHOICES]
            if new_status not in allowed_statuses:
                return JsonResponse(
                    {
                        "status": "error",
                        "error": f"Invalid status '{new_status}'. Allowed: {', '.join(allowed_statuses)}",
                    },
                    status=400,
                )
            case.status = new_status

        if "priority" in body:
            new_p = body["priority"]
            if new_p not in ["high", "medium", "low"]:
                return JsonResponse(
                    {"status": "error", "error": f"Invalid priority '{new_p}'. Allowed: high, medium, low"},
                    status=400,
                )
            case.priority = new_p

        if "assigned_team" in body:
            case.assigned_team = str(body["assigned_team"]).strip()

        if "notes" in body:
            case.notes = str(body["notes"]).strip()

        case.save()

    return JsonResponse(
        {
            "status": "success",
            "case": {
                "case_id": case.case_id,
                "observation_id": case.observation.observation_id if case.observation else None,
                "location_name": case.location_name,
                "forest_range": case.forest_range,
                "latitude": case.latitude,
                "longitude": case.longitude,
                "priority": case.priority,
                "status": case.status,
                "assigned_team": case.assigned_team,
                "notes": case.notes,
                "created_at": case.created_at.isoformat(),
                "updated_at": case.updated_at.isoformat(),
            },
        }
    )



# In-memory tile cache to minimize external network requests
TILE_CACHE = {}


def api_map_tile_proxy(request, z, x, y):
    """
    Compliant Tile Proxy endpoint for OpenStreetMap raster tiles.
    Complies with OpenStreetMap Foundation Tile Usage Policy:
    - Attaches a documented, unique User-Agent identifier
    - Caches tiles in memory to avoid repeated requests
    - Validates zoom and coordinate boundaries
    - Falls back to Carto Voyager CDN if upstream OSM is throttled or unreachable
    """
    import urllib.request
    import urllib.error

    if not (0 <= z <= 19) or x < 0 or y < 0:
        return HttpResponse("Invalid tile coordinates", status=400)

    cache_key = f"{z}_{x}_{y}"
    if cache_key in TILE_CACHE:
        return HttpResponse(TILE_CACHE[cache_key], content_type="image/png")

    osm_url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    headers = {
        "User-Agent": "VanDrishti/1.0 (Forest Fire Monitoring Prototype; Nainital Division; https://github.com/VanDrishti; research@vandrishti.org)"
    }
    req = urllib.request.Request(osm_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            resp_headers = dict(response.info())
            if "x-blocked" in resp_headers:
                raise ValueError("OSM tile blocked")
            tile_bytes = response.read()
            if len(TILE_CACHE) < 1000:
                TILE_CACHE[cache_key] = tile_bytes
            return HttpResponse(tile_bytes, content_type="image/png")
    except Exception:
        # Fallback to Carto Voyager
        try:
            fallback_url = f"https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
            req2 = urllib.request.Request(fallback_url, headers={"User-Agent": "VanDrishti/1.0"})
            with urllib.request.urlopen(req2, timeout=4) as response2:
                tile_bytes = response2.read()
                if len(TILE_CACHE) < 1000:
                    TILE_CACHE[cache_key] = tile_bytes
                return HttpResponse(tile_bytes, content_type="image/png")
        except Exception as exc:
            return HttpResponse(f"Tile fetch failed: {str(exc)}", status=502)
