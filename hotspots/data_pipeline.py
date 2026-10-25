"""
VanDrishti — Data Validation & Pipeline Integration Module
Nainital Forest Division, Uttarakhand, India

Handles data ingestion, coordinate boundary checking, schema validation,
and separation of real, historical, and simulated satellite observations.
"""

import csv
import os
from datetime import datetime, date, time, timezone as dt_timezone
from django.utils import timezone
from .models import Observation, TelemetrySync
from .scoring import calculate_priority_score, NAINITAL_BOUNDS


def parse_firms_datetime(date_str, time_str=None):
    """
    Parses FIRMS acquisition date and time or ISO timestamp string into a timezone-aware datetime.
    Supports:
    - datetime / date objects directly
    - Separate date (YYYY-MM-DD) and 4-digit time (e.g. '0835' or '835')
    - ISO timestamps: '2026-05-14T08:35:00Z', '2026-05-14 08:35:00'
    - Date-only strings
    """
    if not date_str:
        return None

    if isinstance(date_str, datetime):
        if date_str.tzinfo is None:
            return date_str.replace(tzinfo=dt_timezone.utc)
        return date_str

    if isinstance(date_str, date):
        base_time = time(0, 0)
        if time_str is not None and str(time_str).strip():
            t_str = str(time_str).strip().zfill(4)
            try:
                base_time = time(int(t_str[:2]), int(t_str[2:4]))
            except Exception:
                pass
        return datetime.combine(date_str, base_time, tzinfo=dt_timezone.utc)

    s = str(date_str).strip()

    # Case 1: Separate FIRMS acq_time (e.g., '0835' -> 08:35 UTC)
    if time_str is not None and str(time_str).strip():
        t_str = str(time_str).strip().zfill(4)
        try:
            hour = int(t_str[:2])
            minute = int(t_str[2:4])
            base_date = datetime.strptime(s, "%Y-%m-%d").date()
            return datetime.combine(base_date, time(hour, minute), tzinfo=dt_timezone.utc)
        except Exception:
            pass

    # Case 2: Attempt standard fromisoformat
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt
    except ValueError:
        pass

    # Case 3: Standard strptime formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            return dt
        except ValueError:
            continue

    return None


def normalize_confidence(conf_val):
    """
    Normalizes confidence to an integer (0-100).
    NASA FIRMS VIIRS often outputs categorical confidence:
    'l' -> low (30%), 'n' -> nominal (65%), 'h' -> high (90%).
    MODIS outputs numerical percentage (0-100).
    """
    if conf_val is None or conf_val == "":
        return None

    s = str(conf_val).strip().lower()
    if s == "l" or s == "low":
        return 30
    elif s == "n" or s == "nominal":
        return 65
    elif s == "h" or s == "high":
        return 90

    try:
        val = int(round(float(s)))
        return max(0, min(100, val))
    except (ValueError, TypeError):
        return None


def normalize_data_nature(nature_str, default="historical"):
    """
    Normalizes data classification across:
    - 'simulated' / 'sample' / 'benchmark' -> 'simulated'
    - 'historical' / 'archive' -> 'historical'
    - 'real' / 'live' / 'latest' / 'latest-available' -> 'real'
    """
    if not nature_str:
        return default

    s = str(nature_str).strip().lower()
    if s in ["sample", "benchmark", "simulated"]:
        return "simulated"
    elif s in ["historical", "archive", "archival"]:
        return "historical"
    elif s in ["real", "live", "latest", "latest-available", "firms"]:
        return "real"
    return default if default in ["simulated", "historical", "real"] else "historical"


def normalize_status(status_str, default="new"):
    """
    Normalizes review / response status:
    - 'new', 'assigned' -> 'new'
    - 'under-review', 'under review', 'review', 'checking' -> 'under-review'
    - 'verified' -> 'verified'
    - 'closed', 'resolved' -> 'closed'
    """
    if not status_str:
        return default

    s = str(status_str).strip().lower()
    if s in ["under_review", "under review", "review", "checking"]:
        return "under-review"
    elif s in ["verified"]:
        return "verified"
    elif s in ["closed", "resolved"]:
        return "closed"
    elif s in ["new", "assigned"]:
        return "new"
    return default


def validate_observation_record(row, default_data_nature="historical"):
    """
    Validates an incoming raw satellite observation record against schema rules.
    Returns: (is_valid, sanitized_dict_or_error_msg)
    """
    # 1. Coordinates check
    lat_raw = row.get("latitude") if "latitude" in row else row.get("lat")
    lng_raw = row.get("longitude") if "longitude" in row else (row.get("lon") if "lon" in row else row.get("lng"))

    if lat_raw is None or lng_raw is None or str(lat_raw).strip() == "" or str(lng_raw).strip() == "":
        return False, "Missing required latitude or longitude coordinates."

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (ValueError, TypeError):
        return False, f"Malformed coordinates: lat='{lat_raw}', lng='{lng_raw}'"

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return False, f"Coordinates out of earthly range: ({lat}, {lng})"

    # Bounding check for Nainital Division
    in_nainital = (
        NAINITAL_BOUNDS["min_lat"] <= lat <= NAINITAL_BOUNDS["max_lat"]
        and NAINITAL_BOUNDS["min_lng"] <= lng <= NAINITAL_BOUNDS["max_lng"]
    )

    # 2. Observation timestamp
    dt_raw = row.get("observation_time") or row.get("detected_at") or row.get("acq_date")
    t_raw = row.get("acq_time")
    detected_at = parse_firms_datetime(dt_raw, t_raw)
    if not detected_at:
        return False, f"Unparseable observation timestamp: '{dt_raw}'"

    # 3. Confidence & FRP
    confidence = normalize_confidence(row.get("confidence"))

    frp_raw = row.get("frp") or row.get("frp_mw")
    frp = None
    if frp_raw is not None and str(frp_raw).strip() != "":
        try:
            frp = max(0.0, float(frp_raw))
        except (ValueError, TypeError):
            frp = None

    brightness_raw = row.get("brightness") or row.get("brightness_temp_k") or row.get("bright_ti4")
    brightness_temp_k = None
    if brightness_raw is not None and str(brightness_raw).strip() != "":
        try:
            brightness_temp_k = float(brightness_raw)
        except (ValueError, TypeError):
            brightness_temp_k = None

    # 4. Identification & Satellite Source
    obs_id = (
        row.get("observation_id")
        or row.get("id")
        or f"VD-FIRMS-{detected_at.strftime('%Y%m%d')}-{int(lat*100)%1000:03d}{int(lng*100)%1000:03d}"
    )

    satellite_source = (
        row.get("satellite_source")
        or row.get("satellite")
        or row.get("instrument")
        or "NASA FIRMS (VIIRS/MODIS)"
    )

    location_name = row.get("location_name") or row.get("location") or "Nainital Beat"
    forest_range = row.get("forest_range") or row.get("range") or "Nainital Forest Division"

    # 5. Data Nature (real, historical, simulated)
    data_nature = normalize_data_nature(row.get("data_nature"), default=default_data_nature)
    status = normalize_status(row.get("status"), default="new")
    notes = row.get("notes") or ""

    sanitized = {
        "observation_id": obs_id,
        "latitude": round(lat, 5),
        "longitude": round(lng, 5),
        "location_name": location_name,
        "forest_range": forest_range,
        "detected_at": detected_at,
        "satellite_source": satellite_source,
        "confidence": confidence,
        "brightness_temp_k": brightness_temp_k,
        "frp_mw": frp,
        "status": status,
        "data_nature": data_nature,
        "notes": notes,
        "in_nainital_bounds": in_nainital,
        "priority_score": row.get("priority_score"),
        "priority": row.get("priority"),
    }
    return True, sanitized


def import_observations_from_csv(csv_file_path, default_data_nature="historical", dry_run=False, filter_bounds=False):
    """
    Imports observations from a CSV file into the database.
    Calculates priority scores and updates TelemetrySync.

    Args:
        csv_file_path: File system path to the CSV file.
        default_data_nature: 'historical', 'simulated', or 'real'.
        dry_run: When True, validates schema without database writes.
        filter_bounds: When True, skips observations outside the documented Nainital bounding box.
    """
    if not os.path.exists(csv_file_path):
        return {
            "success": False,
            "error": f"File not found: {csv_file_path}",
            "total_rows": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [f"File not found: {csv_file_path}"],
        }

    default_data_nature = normalize_data_nature(default_data_nature, default="historical")

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    try:
        with open(csv_file_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed to read CSV: {str(exc)}",
            "total_rows": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [f"Failed to read CSV: {str(exc)}"],
        }

    if not rows:
        return {
            "success": True,
            "total_rows": 0,
            "valid_records": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": ["CSV file contains no data rows."],
            "dry_run": dry_run,
        }

    # First validate all rows
    valid_records = []
    for idx, row in enumerate(rows, start=1):
        if "data_nature" not in row or not row["data_nature"]:
            row["data_nature"] = default_data_nature

        is_valid, result = validate_observation_record(row, default_data_nature=default_data_nature)
        if not is_valid:
            errors.append(f"Row {idx}: {result}")
            skipped_count += 1
            continue

        if filter_bounds and not result["in_nainital_bounds"]:
            skipped_count += 1
            errors.append(f"Row {idx} ({result['observation_id']}): Skipped outside Nainital boundary ({result['latitude']}, {result['longitude']}).")
            continue

        valid_records.append(result)

    if dry_run:
        return {
            "success": True,
            "total_rows": len(rows),
            "valid_records": len(valid_records),
            "skipped": skipped_count,
            "errors": errors,
            "dry_run": True,
        }

    # Second pass: calculate priority scores with cluster context
    for rec in valid_records:
        score_info = calculate_priority_score(rec, all_observations=valid_records)
        priority_category = score_info["category"]

        # If priority was not explicitly forced in CSV, use calculated category
        if not rec.get("priority"):
            rec["priority"] = priority_category

        # Add notes if empty
        if not rec["notes"]:
            rec["notes"] = f"Ingested via VanDrishti pipeline. {score_info['explanation_summary']}"

        obj, created = Observation.objects.update_or_create(
            observation_id=rec["observation_id"],
            defaults={
                "latitude": rec["latitude"],
                "longitude": rec["longitude"],
                "location_name": rec["location_name"],
                "forest_range": rec["forest_range"],
                "detected_at": rec["detected_at"],
                "satellite_source": rec["satellite_source"],
                "confidence": rec["confidence"],
                "brightness_temp_k": rec["brightness_temp_k"],
                "frp_mw": rec["frp_mw"],
                "priority": rec["priority"],
                "status": rec["status"],
                "data_nature": rec["data_nature"],
                "notes": rec["notes"],
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    # Update TelemetrySync status
    now = timezone.now()
    telemetry, _ = TelemetrySync.objects.get_or_create(
        division_name="Nainital Forest Division",
        defaults={
            "source_name": "NASA FIRMS (VIIRS / MODIS)",
            "status": "connected",
            "last_sync_time": now,
        },
    )
    telemetry.last_sync_time = now
    telemetry.status = "connected"
    telemetry.observations_ingested = Observation.objects.count()
    telemetry.save()

    return {
        "success": True,
        "total_rows": len(rows),
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "errors": errors,
    }
