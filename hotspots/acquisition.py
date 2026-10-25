"""
VanDrishti — NASA FIRMS Satellite Telemetry Acquisition Module
Nainital Forest Division, Uttarakhand, India

Fetches near-real-time active fire and thermal anomaly hotspot data from the official
NASA FIRMS (Fire Information for Resource Management System) REST API, normalizes the
observations, filters by Nainital boundary, and scores records through the explainable
priority engine.
"""

import csv
import io
import math
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone as dt_timezone
from django.conf import settings
from django.utils import timezone

from .models import Observation, TelemetrySync
from .scoring import NAINITAL_BOUNDS, calculate_priority_score, in_nainital_bounds, haversine_distance_km
from .data_pipeline import validate_observation_record


DEFAULT_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"
DEFAULT_SOURCE = "VIIRS_SNPP_NRT"
SUPPORTED_SOURCES = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT",
    "MODIS_SP",
    "LANDSAT_NRT",
]
DEFAULT_TIMEOUT_SECONDS = 15

# Known reference landmarks in Nainital Forest Division for beat context enrichment
NAINITAL_REFERENCE_LANDMARKS = [
    {"name": "China Peak (Naina Peak) Ridge", "range": "Nainital Central Range", "lat": 29.3952, "lng": 79.4418},
    {"name": "Kilbury & Pangot Forest Belt", "range": "Kilbury Oak Forest Range", "lat": 29.4124, "lng": 79.4682},
    {"name": "Bhowali Sanatorium Hillside", "range": "Bhowali Forest Range", "lat": 29.3621, "lng": 79.4935},
    {"name": "Gagar Pass Eastern Flank", "range": "Ramgarh Forest Range", "lat": 29.4285, "lng": 79.5241},
    {"name": "Sukhatal Valley Perimeter", "range": "Nainital South Range", "lat": 29.3510, "lng": 79.4312},
    {"name": "Mukteshwar Ridge Forest Beat", "range": "Mukteshwar Range", "lat": 29.3789, "lng": 79.5492},
    {"name": "Snow View - Lariakanta Trail", "range": "Nainital Central Range", "lat": 29.3845, "lng": 79.4560},
    {"name": "Jyolikote Forest Descent", "range": "Nainital South Range", "lat": 29.3402, "lng": 79.4815},
    {"name": "Pangot Valley Trail", "range": "Kilbury Oak Forest Range", "lat": 29.4180, "lng": 79.4750},
    {"name": "Khurpatal Overlook", "range": "Nainital South Range", "lat": 29.3550, "lng": 79.4480},
]


def get_firms_api_key():
    """
    Retrieve NASA FIRMS MAP_KEY / API key securely from environment variables
    or Django settings. Returns None if not configured.
    """
    key = os.environ.get("FIRMS_MAP_KEY") or os.environ.get("NASA_FIRMS_API_KEY")
    if not key:
        key = getattr(settings, "FIRMS_MAP_KEY", None) or getattr(settings, "NASA_FIRMS_API_KEY", None)
    return key.strip() if key and str(key).strip() else None


def map_firms_satellite_name(satellite_code, instrument=""):
    """
    Expands FIRMS single-letter satellite codes into readable labels.
    - N -> Suomi-NPP (VIIRS)
    - 1 / J1 -> NOAA-20 (VIIRS)
    - 2 / J2 -> NOAA-21 (VIIRS)
    - A -> Aqua (MODIS)
    - T -> Terra (MODIS)
    """
    code = str(satellite_code or "").strip().upper()
    inst = str(instrument or "").strip().upper()
    mapping = {
        "N": "VIIRS (Suomi-NPP)",
        "1": "VIIRS (NOAA-20)",
        "J1": "VIIRS (NOAA-20)",
        "2": "VIIRS (NOAA-21)",
        "J2": "VIIRS (NOAA-21)",
        "A": "MODIS (Aqua)",
        "T": "MODIS (Terra)",
    }
    if code in mapping:
        return mapping[code]
    if code:
        return f"{inst} ({code})" if inst else code
    return f"NASA FIRMS ({inst})" if inst else "NASA FIRMS (VIIRS/MODIS)"


def estimate_nainital_location(lat, lng):
    """
    Finds the closest known Nainital forest landmark or beat based on coordinates
    to provide meaningful geographic context for field beat teams.
    """
    try:
        f_lat = float(lat)
        f_lng = float(lng)
    except (ValueError, TypeError):
        return "Nainital Division Beat", "Nainital Forest Division"

    best_landmark = None
    min_dist = float("inf")

    for lm in NAINITAL_REFERENCE_LANDMARKS:
        dist = haversine_distance_km(f_lat, f_lng, lm["lat"], lm["lng"])
        if dist < min_dist:
            min_dist = dist
            best_landmark = lm

    if best_landmark and min_dist <= 8.0:
        return f"Near {best_landmark['name']}", best_landmark["range"]
    return "Nainital Division Beat", "Nainital Forest Division"


def build_firms_area_url(map_key, source=DEFAULT_SOURCE, bounds=None, day_range=1, date=None, base_url=None):
    """
    Builds the official NASA FIRMS Area API URL for CSV export.
    Format: https://firms.modaps.eosdis.nasa.gov/api/area/csv/[MAP_KEY]/[SOURCE]/[EXTENT]/[DAYS]/[DATE]
    Extent is: west,south,east,north (min_lng,min_lat,max_lng,max_lat).
    """
    if not map_key or not str(map_key).strip():
        raise ValueError("A valid NASA FIRMS MAP_KEY is required to build the API URL.")

    base = (base_url or getattr(settings, "FIRMS_API_BASE_URL", None) or DEFAULT_BASE_URL).rstrip("/")
    source_name = source or DEFAULT_SOURCE
    b = bounds or NAINITAL_BOUNDS

    # Format bounds as west,south,east,north
    extent = f"{b['min_lng']},{b['min_lat']},{b['max_lng']},{b['max_lat']}"
    try:
        days = max(1, min(10, int(day_range)))
    except (ValueError, TypeError):
        days = 1

    url = f"{base}/area/csv/{map_key.strip()}/{source_name}/{extent}/{days}"
    if date:
        if isinstance(date, datetime):
            date_str = date.strftime("%Y-%m-%d")
        else:
            date_str = str(date).strip()
        url = f"{url}/{date_str}"

    return url


def fetch_firms_csv(api_key=None, map_key=None, source=DEFAULT_SOURCE, bounds=None, day_range=1, date=None, timeout=None):
    """
    Performs the HTTP request to the NASA FIRMS Area API and returns raw CSV text.
    Handles missing keys, HTTP errors, timeouts, and connection errors gracefully.

    Returns:
        dict with keys:
            - success (bool)
            - status (str or None)
            - error (str or None)
            - status_code (int or None)
            - raw_data (str)
            - url (str or None)
    """
    key = api_key or map_key or get_firms_api_key()
    if not key or not str(key).strip():
        return {
            "success": False,
            "status": "missing_credentials",
            "error": (
                "NASA FIRMS API key is missing. FIRMS_MAP_KEY is not configured. "
                "Set the FIRMS_MAP_KEY environment variable or specify a key. "
                "A free MAP_KEY can be requested at https://firms.modaps.eosdis.nasa.gov/api/."
            ),
            "status_code": None,
            "raw_data": "",
            "url": None,
        }

    timeout_val = timeout or getattr(settings, "FIRMS_REQUEST_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
    b = bounds or NAINITAL_BOUNDS

    try:
        url = build_firms_area_url(
            map_key=key,
            source=source,
            bounds=b,
            day_range=day_range,
            date=date,
        )
    except Exception as exc:
        return {
            "success": False,
            "status": "api_error",
            "error": f"Failed to construct FIRMS API URL: {exc}",
            "status_code": None,
            "raw_data": "",
            "url": None,
        }

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VanDrishti-FireMonitoring/1.0 (Nainital Forest Division; Python/Django)",
            "Accept": "text/csv, text/plain",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_val) as resp:
            try:
                code_val = resp.getcode()
                status_code = int(code_val) if code_val and not hasattr(code_val, "_mock_return_value") else 200
            except Exception:
                status_code = 200

            raw_bytes = resp.read()
            try:
                raw_text = raw_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                raw_text = raw_bytes.decode("latin1")

            stripped_text = raw_text.strip()
            lower_text = stripped_text.lower()
            lines = [ln.strip() for ln in stripped_text.splitlines() if ln.strip()]

            if lines and "latitude" not in lines[0].lower():
                if "invalid map key" in lower_text or "invalid map_key" in lower_text:
                    return {
                        "success": False,
                        "status": "auth_error",
                        "error": "NASA FIRMS API authentication failed: Provided MAP_KEY is invalid or inactive.",
                        "status_code": 401,
                        "raw_data": raw_text,
                        "url": url,
                    }
                if "invalid source" in lower_text:
                    return {
                        "success": False,
                        "status": "invalid_source",
                        "error": f"NASA FIRMS API error: Unsupported or invalid satellite source '{source}'.",
                        "status_code": 400,
                        "raw_data": raw_text,
                        "url": url,
                    }
                if "invalid area" in lower_text:
                    return {
                        "success": False,
                        "status": "invalid_area",
                        "error": "NASA FIRMS API error: Invalid geographic bounding coordinates.",
                        "status_code": 400,
                        "raw_data": raw_text,
                        "url": url,
                    }
                error_msg = lines[0][:200]
                return {
                    "success": False,
                    "status": "api_error_response",
                    "error": f"NASA FIRMS API returned error: {error_msg}",
                    "status_code": 400,
                    "raw_data": raw_text,
                    "url": url,
                }

            return {
                "success": True,
                "status": "ok",
                "error": None,
                "status_code": status_code,
                "raw_data": raw_text,
                "url": url,
            }

    except urllib.error.HTTPError as http_err:
        code = http_err.code
        if code in (401, 403):
            msg = f"NASA FIRMS API authentication failed (HTTP {code}): Invalid, inactive, or rate-limited MAP_KEY."
        elif code == 404:
            msg = f"NASA FIRMS API endpoint not found (HTTP 404): Check API URL structure."
        elif code >= 500:
            msg = f"NASA FIRMS remote server error (HTTP {code}): Service temporarily unavailable."
        else:
            msg = f"NASA FIRMS API returned HTTP error {code}: {http_err.reason}."
        return {
            "success": False,
            "status": "http_error",
            "error": msg,
            "status_code": code,
            "raw_data": "",
            "url": url,
        }

    except (urllib.error.URLError, socket.timeout, TimeoutError) as net_err:
        reason = getattr(net_err, "reason", None)
        if isinstance(reason, socket.timeout) or isinstance(net_err, (socket.timeout, TimeoutError)):
            msg = f"Request to NASA FIRMS API timed out after {timeout_val} seconds."
        else:
            msg = f"Network connection error to NASA FIRMS API: {reason or net_err}."
        return {
            "success": False,
            "status": "api_error",
            "error": msg,
            "status_code": None,
            "raw_data": "",
            "url": url,
        }

    except Exception as generic_err:
        return {
            "success": False,
            "status": "api_error",
            "error": f"Failed to connect to NASA FIRMS API: {generic_err}",
            "status_code": None,
            "raw_data": "",
            "url": url,
        }


def parse_firms_csv_to_records(csv_text, default_source=DEFAULT_SOURCE):
    """
    Parses FIRMS CSV text into raw observation dictionaries, enriching them with
    sensor labels, beat landmarks, and metadata for pipeline validation.
    """
    if not csv_text or not csv_text.strip():
        return []

    stream = io.StringIO(csv_text.strip())
    reader = csv.DictReader(stream)
    records = []

    for row in reader:
        lat = row.get("latitude") or row.get("lat")
        lng = row.get("longitude") or row.get("lon") or row.get("lng")
        if lat is None or lng is None:
            continue

        sat_label = map_firms_satellite_name(
            row.get("satellite"),
            instrument=row.get("instrument") or default_source,
        )
        if "NASA FIRMS" in sat_label:
            satellite_source = sat_label
        else:
            satellite_source = f"NASA FIRMS ({sat_label})"

        loc_from_csv = row.get("location_name") or row.get("location")
        range_from_csv = row.get("forest_range") or row.get("range")
        if not loc_from_csv or not range_from_csv:
            est_loc, est_range = estimate_nainital_location(lat, lng)
            location_name = loc_from_csv or est_loc
            forest_range = range_from_csv or est_range
        else:
            location_name = loc_from_csv
            forest_range = range_from_csv

        enriched_row = dict(row)
        enriched_row["satellite_source"] = satellite_source
        enriched_row["location_name"] = location_name
        enriched_row["forest_range"] = forest_range
        enriched_row["data_nature"] = "real"
        enriched_row["status"] = "new"

        records.append(enriched_row)

    return records


def acquire_and_ingest_firms_hotspots(
    api_key=None,
    map_key=None,
    source=DEFAULT_SOURCE,
    bounds=None,
    area_bbox=None,
    day_range=1,
    date=None,
    timeout=None,
    dry_run=False,
    filter_bounds=True,
):
    """
    Full pipeline to fetch live NASA FIRMS hotspot data for Nainital, validate schema,
    filter by Nainital boundaries, score priorities, and persist to database.
    Preserves case-management workflow status ('under-review', 'verified') and custom review notes.

    Returns:
        dict containing:
            - success (bool)
            - status (str)
            - error (str or None)
            - total_records / total_rows (int)
            - valid_records (int)
            - created (int)
            - updated (int)
            - skipped (int)
            - errors (list of str)
            - dry_run (bool)
    """
    if bounds is None and area_bbox is not None:
        if isinstance(area_bbox, str):
            try:
                parts = [float(p.strip()) for p in area_bbox.split(",")]
                if len(parts) == 4:
                    bounds = {
                        "min_lng": parts[0],
                        "min_lat": parts[1],
                        "max_lng": parts[2],
                        "max_lat": parts[3],
                    }
            except Exception:
                bounds = NAINITAL_BOUNDS
        elif isinstance(area_bbox, dict):
            bounds = area_bbox
    if bounds is None:
        bounds = NAINITAL_BOUNDS

    resolved_key = api_key or map_key or get_firms_api_key()

    try:
        resolved_days = max(1, min(10, int(day_range)))
    except (ValueError, TypeError):
        resolved_days = 1

    fetch_res = fetch_firms_csv(
        api_key=resolved_key,
        source=source,
        bounds=bounds,
        day_range=resolved_days,
        date=date,
        timeout=timeout,
    )

    if not fetch_res["success"]:
        status = fetch_res.get("status") or "api_error"
        if not dry_run and status != "missing_credentials":
            now = timezone.now()
            telemetry, _ = TelemetrySync.objects.get_or_create(
                division_name="Nainital Forest Division",
                defaults={
                    "source_name": f"NASA FIRMS ({source})",
                    "status": "error",
                    "last_sync_time": now,
                    "notes": fetch_res["error"][:250],
                },
            )
            telemetry.status = "error"
            telemetry.source_name = f"NASA FIRMS ({source})"
            telemetry.notes = fetch_res["error"][:250]
            telemetry.save()

        return {
            "success": False,
            "status": status,
            "error": fetch_res["error"],
            "total_records": 0,
            "total_rows": 0,
            "valid_records": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [fetch_res["error"]],
            "dry_run": dry_run,
        }

    raw_csv = fetch_res["raw_data"]
    parsed_rows = parse_firms_csv_to_records(raw_csv, default_source=source)

    if not parsed_rows:
        if not dry_run:
            now = timezone.now()
            telemetry, _ = TelemetrySync.objects.get_or_create(
                division_name="Nainital Forest Division",
                defaults={
                    "source_name": f"NASA FIRMS ({source})",
                    "status": "connected",
                    "last_sync_time": now,
                },
            )
            telemetry.last_sync_time = now
            telemetry.status = "connected"
            telemetry.source_name = f"NASA FIRMS ({source})"
            telemetry.save()

        extent = f"{bounds['min_lng']},{bounds['min_lat']},{bounds['max_lng']},{bounds['max_lat']}"
        return {
            "success": True,
            "status": "no_anomalies",
            "message": f"Zero thermal anomalies detected by {source} in Nainital bounding box ({extent}) over the last {resolved_days} day(s).",
            "total_records": 0,
            "total_rows": 0,
            "valid_records": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [],
            "dry_run": dry_run,
        }

    valid_records = []
    skipped_count = 0
    errors = []

    # First pass: schema validation and Nainital boundary filtering
    for idx, row in enumerate(parsed_rows, start=1):
        is_valid, result = validate_observation_record(row)
        if not is_valid:
            errors.append(f"FIRMS Record {idx}: {result}")
            skipped_count += 1
            continue

        if filter_bounds and not result.get("in_nainital_bounds"):
            errors.append(
                f"FIRMS Record {idx}: Coordinates ({result['latitude']}, {result['longitude']}) "
                f"are outside Nainital Forest Division boundary."
            )
            skipped_count += 1
            continue

        valid_records.append(result)

    if dry_run:
        return {
            "success": True,
            "status": "dry_run",
            "total_records": len(parsed_rows),
            "total_rows": len(parsed_rows),
            "valid_records": len(valid_records),
            "created": 0,
            "updated": 0,
            "skipped": skipped_count,
            "errors": errors,
            "dry_run": True,
        }

    # Second pass: calculate explainable priority scores with cluster context & persist
    created_count = 0
    updated_count = 0

    for rec in valid_records:
        score_info = calculate_priority_score(rec, all_observations=valid_records)
        priority_category = score_info["category"]

        if not rec.get("priority"):
            rec["priority"] = priority_category

        frp_str = f"{rec['frp_mw']} MW" if rec.get("frp_mw") is not None else "N/A"
        bright_str = f"{rec['brightness_temp_k']} K" if rec.get("brightness_temp_k") is not None else "N/A"

        if not rec.get("notes"):
            rec["notes"] = (
                f"Live satellite telemetry ingested via NASA FIRMS ({source}). "
                f"Possible thermal anomaly detected by sensor (FRP: {frp_str}, Brightness: {bright_str}). "
                f"{score_info['explanation_summary']}"
            )

        obs_id = str(rec["observation_id"])[:32]
        existing_obs = Observation.objects.filter(observation_id=obs_id).first()

        defaults = {
            "latitude": rec["latitude"],
            "longitude": rec["longitude"],
            "location_name": rec["location_name"][:150],
            "forest_range": rec["forest_range"][:100],
            "detected_at": rec["detected_at"],
            "satellite_source": rec["satellite_source"][:60],
            "confidence": rec["confidence"],
            "brightness_temp_k": rec["brightness_temp_k"],
            "frp_mw": rec["frp_mw"],
            "priority": rec["priority"],
            "data_nature": "real",
        }

        if existing_obs:
            # Preserve existing case-management workflow status and user-edited review notes
            defaults["status"] = existing_obs.status
            defaults["notes"] = existing_obs.notes or rec["notes"]
        else:
            defaults["status"] = rec.get("status") or "new"
            defaults["notes"] = rec["notes"]

        obj, created = Observation.objects.update_or_create(
            observation_id=obs_id,
            defaults=defaults,
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    # Update TelemetrySync status to connected
    now = timezone.now()
    telemetry, _ = TelemetrySync.objects.get_or_create(
        division_name="Nainital Forest Division",
        defaults={
            "source_name": f"NASA FIRMS ({source})",
            "status": "connected",
            "last_sync_time": now,
        },
    )
    telemetry.last_sync_time = now
    telemetry.status = "connected"
    telemetry.source_name = f"NASA FIRMS ({source})"
    telemetry.observations_ingested = Observation.objects.count()
    telemetry.notes = (
        f"Active telemetry sync completed at {now.strftime('%H:%M:%S UTC')}. "
        f"Ingested {created_count} new, {updated_count} updated observations from FIRMS."
    )
    telemetry.save()

    return {
        "success": True,
        "status": "success",
        "total_records": len(parsed_rows),
        "total_rows": len(parsed_rows),
        "valid_records": len(valid_records),
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "errors": errors,
        "dry_run": False,
    }
