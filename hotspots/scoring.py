"""
VanDrishti — Explainable Priority Scoring Engine
Nainital Forest Division, Uttarakhand, India

Calculates transparent, rule-based prototype priority scores for satellite thermal observations
using documented prototype design weights:
- Confidence: 30%
- Nearby repeat detections: 30%
- Fire Radiative Power (FRP): 15%
- Recency: 15%
- Forest-area overlap: 10%

Note: These are prototype decision-support weights for research and operational evaluation,
not official government fire-risk thresholds. Hotspot observations indicate potential thermal
anomalies, not confirmed forest fires.
"""

import math
from datetime import datetime, timezone as dt_timezone
from django.utils import timezone


WEIGHT_CONFIDENCE = 0.30
WEIGHT_REPEAT = 0.30
WEIGHT_FRP = 0.15
WEIGHT_RECENCY = 0.15
WEIGHT_FOREST_OVERLAP = 0.10

# Documented Bounding box for Nainital Forest Division
NAINITAL_BOUNDS = {
    "min_lat": 29.10,
    "max_lat": 29.65,
    "min_lng": 79.10,
    "max_lng": 79.80,
}

KNOWN_FOREST_RANGES = [
    "nainital",
    "kilbury",
    "bhowali",
    "ramgarh",
    "mukteshwar",
    "china peak",
    "naina peak",
    "sukhatal",
    "jyolikote",
    "pangot",
    "lariakanta",
    "snow view",
]


def in_nainital_bounds(lat_or_record, lng=None):
    """
    Check if a coordinate pair (latitude, longitude) or an observation object/dict
    falls within the Nainital Forest Division bounding box.

    Accepts:
    - Two positional args: in_nainital_bounds(lat, lng)
    - A dict: in_nainital_bounds({'latitude': ..., 'longitude': ...})
    - A model instance: in_nainital_bounds(observation_obj)
    """
    if lng is None:
        if isinstance(lat_or_record, dict):
            lat = lat_or_record.get("latitude") if "latitude" in lat_or_record else lat_or_record.get("lat")
            lng = (
                lat_or_record.get("longitude")
                if "longitude" in lat_or_record
                else (lat_or_record.get("lng") or lat_or_record.get("lon"))
            )
        else:
            lat = getattr(lat_or_record, "latitude", None)
            if lat is None:
                lat = getattr(lat_or_record, "lat", None)
            lng = getattr(lat_or_record, "longitude", None)
            if lng is None:
                lng = getattr(lat_or_record, "lng", None)
                if lng is None:
                    lng = getattr(lat_or_record, "lon", None)
    else:
        lat = lat_or_record

    try:
        f_lat = float(lat)
        f_lng = float(lng)
        return (
            NAINITAL_BOUNDS["min_lat"] <= f_lat <= NAINITAL_BOUNDS["max_lat"]
            and NAINITAL_BOUNDS["min_lng"] <= f_lng <= NAINITAL_BOUNDS["max_lng"]
        )
    except (ValueError, TypeError):
        return False


def haversine_distance_km(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance in kilometers between two points
    on the earth (specified in decimal degrees).
    """
    try:
        lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
        c = 2.0 * math.asin(math.sqrt(a))
        r = 6371.0  # Radius of earth in kilometers
        return c * r
    except (TypeError, ValueError):
        return float("inf")


def calculate_priority_score(observation, all_observations=None, reference_time=None):
    """
    Calculates the explainable priority score and detailed component breakdown
    for an Observation model instance or dictionary.

    Args:
        observation: Observation model instance or dict with observation fields.
        all_observations: Optional list/iterable of other observations for spatial repeat calculation.
        reference_time: Optional datetime reference (defaults to timezone.now()).

    Returns:
        dict containing:
            - score (float 0-100)
            - category ('high', 'medium', 'low')
            - components (dict of 5 component details)
            - available_weight (float, total weight of available inputs)
            - is_complete (bool, whether all 5 components were fully available)
            - explanation_summary (str, concise human-readable summary)
    """
    if reference_time is None:
        reference_time = timezone.now()

    # Normalize access whether model instance or dict
    if isinstance(observation, dict):
        obs_id = observation.get("observation_id") or observation.get("id") or "UNKNOWN"
        lat = observation.get("latitude") if "latitude" in observation else observation.get("lat")
        lng = observation.get("longitude") if "longitude" in observation else observation.get("lng")
        confidence = observation.get("confidence")
        frp = observation.get("frp_mw") if "frp_mw" in observation else observation.get("frp")
        detected_at = observation.get("detected_at") or observation.get("observation_time")
        location_name = observation.get("location_name") or observation.get("location") or ""
        forest_range = observation.get("forest_range") or observation.get("range") or ""
        explicit_score = observation.get("priority_score")
        explicit_priority = observation.get("priority")
    else:
        obs_id = getattr(observation, "observation_id", "UNKNOWN")
        lat = getattr(observation, "latitude", None)
        lng = getattr(observation, "longitude", None)
        confidence = getattr(observation, "confidence", None)
        frp = getattr(observation, "frp_mw", None)
        detected_at = getattr(observation, "detected_at", None)
        location_name = getattr(observation, "location_name", "")
        forest_range = getattr(observation, "forest_range", "")
        # Use __dict__ lookup to prevent triggering property recursion on models
        explicit_score = observation.__dict__.get("priority_score") if hasattr(observation, "__dict__") else None
        explicit_priority = getattr(observation, "priority", None)

    components = {}
    available_weight = 0.0

    # -------------------------------------------------------------------------
    # 1. Detection Confidence (30% weight)
    # -------------------------------------------------------------------------
    if confidence is not None:
        try:
            conf_val = float(confidence)
            # Clamp confidence to [0, 100]
            conf_val = max(0.0, min(100.0, conf_val))
            conf_pts = (conf_val / 100.0) * (WEIGHT_CONFIDENCE * 100.0)
            components["confidence"] = {
                "name": "Sensor Confidence",
                "weight": int(WEIGHT_CONFIDENCE * 100),
                "score": round(conf_pts, 1),
                "max_score": int(WEIGHT_CONFIDENCE * 100),
                "raw_value": f"{int(conf_val)}%",
                "status": "available",
                "explanation": f"{int(conf_val)}% radiometer detection confidence",
            }
            available_weight += WEIGHT_CONFIDENCE
        except (ValueError, TypeError):
            components["confidence"] = {
                "name": "Sensor Confidence",
                "weight": int(WEIGHT_CONFIDENCE * 100),
                "score": 0.0,
                "max_score": int(WEIGHT_CONFIDENCE * 100),
                "raw_value": "Invalid",
                "status": "unavailable",
                "explanation": "Confidence value malformed or invalid",
            }
    else:
        components["confidence"] = {
            "name": "Sensor Confidence",
            "weight": int(WEIGHT_CONFIDENCE * 100),
            "score": 0.0,
            "max_score": int(WEIGHT_CONFIDENCE * 100),
            "raw_value": "Unavailable",
            "status": "unavailable",
            "explanation": "Confidence metric not provided by satellite feed",
        }

    # -------------------------------------------------------------------------
    # 2. Nearby Repeat Detections (30% weight)
    # -------------------------------------------------------------------------
    nearby_cluster_count = 0
    repeat_evaluated = False

    if lat is not None and lng is not None and all_observations:
        try:
            cur_lat = float(lat)
            cur_lng = float(lng)
            repeat_evaluated = True

            for other in all_observations:
                # Skip self
                other_id = other.get("observation_id") if isinstance(other, dict) else getattr(other, "observation_id", None)
                if other_id and other_id == obs_id:
                    continue

                o_lat = other.get("latitude") if isinstance(other, dict) else getattr(other, "latitude", None)
                if o_lat is None and isinstance(other, dict):
                    o_lat = other.get("lat")
                o_lng = other.get("longitude") if isinstance(other, dict) else getattr(other, "longitude", None)
                if o_lng is None and isinstance(other, dict):
                    o_lng = other.get("lng")

                if o_lat is not None and o_lng is not None:
                    try:
                        o_lat_f = float(o_lat)
                        o_lng_f = float(o_lng)
                    except (ValueError, TypeError):
                        continue

                    # Fast bounding box pre-filter (~0.045 deg lat, ~0.055 deg lng ≈ 4 km)
                    if abs(cur_lat - o_lat_f) > 0.045 or abs(cur_lng - o_lng_f) > 0.055:
                        continue

                    dist = haversine_distance_km(cur_lat, cur_lng, o_lat_f, o_lng_f)
                    if dist <= 3.5:  # 3.5 km proximity threshold for thermal cluster
                        nearby_cluster_count += 1
        except Exception:
            repeat_evaluated = False

    if repeat_evaluated:
        if nearby_cluster_count >= 2:
            rep_pts = 30.0
            rep_expl = f"Persistent cluster: {nearby_cluster_count} nearby thermal detections within 3.5 km"
            raw_rep = f"{nearby_cluster_count} repeat detections"
        elif nearby_cluster_count == 1:
            rep_pts = 19.5  # 65% of 30 pts
            rep_expl = "1 nearby repeat detection within 3.5 km"
            raw_rep = "1 repeat detection"
        else:
            rep_pts = 7.5   # 25% of 30 pts baseline
            rep_expl = "Isolated thermal anomaly (no adjacent repeat detections within 3.5 km)"
            raw_rep = "0 nearby detections"

        components["repeat_detections"] = {
            "name": "Nearby Repeat Detections",
            "weight": int(WEIGHT_REPEAT * 100),
            "score": round(rep_pts, 1),
            "max_score": int(WEIGHT_REPEAT * 100),
            "raw_value": raw_rep,
            "status": "available",
            "explanation": rep_expl,
        }
        available_weight += WEIGHT_REPEAT
    else:
        # Fallback if other observations list not provided
        components["repeat_detections"] = {
            "name": "Nearby Repeat Detections",
            "weight": int(WEIGHT_REPEAT * 100),
            "score": 0.0,
            "max_score": int(WEIGHT_REPEAT * 100),
            "raw_value": "Unavailable",
            "status": "unavailable",
            "explanation": "Cluster context unavailable (dataset spatial indexing pending)",
        }

    # -------------------------------------------------------------------------
    # 3. Fire Radiative Power (FRP) (15% weight)
    # Benchmark saturation ceiling is 20.0 MW for mountain pine terrain
    # -------------------------------------------------------------------------
    if frp is not None:
        try:
            frp_val = float(frp)
            frp_val = max(0.0, frp_val)
            normalized_frp = min(frp_val / 20.0, 1.0)
            frp_pts = normalized_frp * (WEIGHT_FRP * 100.0)
            components["frp"] = {
                "name": "Fire Radiative Power",
                "weight": int(WEIGHT_FRP * 100),
                "score": round(frp_pts, 1),
                "max_score": int(WEIGHT_FRP * 100),
                "raw_value": f"{frp_val:.1f} MW",
                "status": "available",
                "explanation": f"{frp_val:.1f} MW radiant thermal energy",
            }
            available_weight += WEIGHT_FRP
        except (ValueError, TypeError):
            components["frp"] = {
                "name": "Fire Radiative Power",
                "weight": int(WEIGHT_FRP * 100),
                "score": 0.0,
                "max_score": int(WEIGHT_FRP * 100),
                "raw_value": "Invalid",
                "status": "unavailable",
                "explanation": "FRP measurement invalid",
            }
    else:
        components["frp"] = {
            "name": "Fire Radiative Power",
            "weight": int(WEIGHT_FRP * 100),
            "score": 0.0,
            "max_score": int(WEIGHT_FRP * 100),
            "raw_value": "Unavailable",
            "status": "unavailable",
            "explanation": "Radiative power (FRP) unavailable from sensor",
        }

    # -------------------------------------------------------------------------
    # 4. Detection Recency (15% weight)
    # -------------------------------------------------------------------------
    if detected_at:
        try:
            if isinstance(detected_at, datetime):
                dt = detected_at
            elif isinstance(detected_at, str):
                s = detected_at.strip()
                try:
                    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                except ValueError:
                    dt = None
                    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                        try:
                            dt = datetime.strptime(s, fmt)
                            break
                        except ValueError:
                            continue
            else:
                dt = None

            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_timezone.utc)
                ref_dt = reference_time
                if ref_dt.tzinfo is None:
                    ref_dt = ref_dt.replace(tzinfo=dt_timezone.utc)

                diff_hours = max(0.0, (ref_dt - dt).total_seconds() / 3600.0)

                if diff_hours <= 2.0:
                    rec_pts = 15.0
                    rec_expl = f"Detected {int(diff_hours * 60)}m ago (immediate recency <= 2h)"
                elif diff_hours <= 6.0:
                    rec_pts = 12.0
                    rec_expl = f"Detected {diff_hours:.1f}h ago (recent window <= 6h)"
                elif diff_hours <= 12.0:
                    rec_pts = 9.0
                    rec_expl = f"Detected {diff_hours:.1f}h ago (within 12h)"
                elif diff_hours <= 24.0:
                    rec_pts = 6.0
                    rec_expl = f"Detected {diff_hours:.1f}h ago (within 24h)"
                elif diff_hours <= 48.0:
                    rec_pts = 3.0
                    rec_expl = f"Detected {diff_hours:.1f}h ago (within 48h)"
                else:
                    rec_pts = 1.0
                    rec_expl = f"Historical observation ({int(diff_hours / 24)} days ago)"

                components["recency"] = {
                    "name": "Detection Recency",
                    "weight": int(WEIGHT_RECENCY * 100),
                    "score": round(rec_pts, 1),
                    "max_score": int(WEIGHT_RECENCY * 100),
                    "raw_value": f"{diff_hours:.1f}h ago" if diff_hours >= 1.0 else f"{int(diff_hours * 60)}m ago",
                    "status": "available",
                    "explanation": rec_expl,
                }
                available_weight += WEIGHT_RECENCY
            else:
                components["recency"] = {
                    "name": "Detection Recency",
                    "weight": int(WEIGHT_RECENCY * 100),
                    "score": 0.0,
                    "max_score": int(WEIGHT_RECENCY * 100),
                    "raw_value": "Unavailable",
                    "status": "unavailable",
                    "explanation": "Detection timestamp unparseable",
                }
        except Exception:
            components["recency"] = {
                "name": "Detection Recency",
                "weight": int(WEIGHT_RECENCY * 100),
                "score": 0.0,
                "max_score": int(WEIGHT_RECENCY * 100),
                "raw_value": "Unavailable",
                "status": "unavailable",
                "explanation": "Detection timestamp unparseable",
            }
    else:
        components["recency"] = {
            "name": "Detection Recency",
            "weight": int(WEIGHT_RECENCY * 100),
            "score": 0.0,
            "max_score": int(WEIGHT_RECENCY * 100),
            "raw_value": "Unavailable",
            "status": "unavailable",
            "explanation": "Detection timestamp unavailable",
        }

    # -------------------------------------------------------------------------
    # 5. Forest-Area Overlap (10% weight)
    # -------------------------------------------------------------------------
    forest_match = False
    is_in_bounds = in_nainital_bounds(lat, lng)

    # Check known reserve forest ranges
    check_text = (location_name + " " + forest_range).lower()
    for kw in KNOWN_FOREST_RANGES:
        if kw in check_text:
            forest_match = True
            break

    if is_in_bounds and forest_match:
        fo_pts = 10.0
        fo_expl = "Coordinates inside Nainital Forest Division reserve canopy"
        raw_fo = forest_range or "Reserve Forest"
        fo_status = "available"
        available_weight += WEIGHT_FOREST_OVERLAP
    elif is_in_bounds:
        fo_pts = 7.0
        fo_expl = "Within Nainital Division boundary (forest compartment verification pending)"
        raw_fo = "Division Boundary"
        fo_status = "available"
        available_weight += WEIGHT_FOREST_OVERLAP
    elif lat is not None and lng is not None:
        fo_pts = 2.0
        fo_expl = "Coordinates outside primary Nainital reserve boundary"
        raw_fo = "Peripheral Zone"
        fo_status = "available"
        available_weight += WEIGHT_FOREST_OVERLAP
    else:
        fo_pts = 0.0
        fo_expl = "Geographic coordinates unavailable for canopy overlap"
        raw_fo = "Unavailable"
        fo_status = "unavailable"

    components["forest_overlap"] = {
        "name": "Forest-Area Overlap",
        "weight": int(WEIGHT_FOREST_OVERLAP * 100),
        "score": round(fo_pts, 1),
        "max_score": int(WEIGHT_FOREST_OVERLAP * 100),
        "raw_value": raw_fo,
        "status": fo_status,
        "explanation": fo_expl,
    }

    # -------------------------------------------------------------------------
    # Final Score Aggregation
    # -------------------------------------------------------------------------
    if explicit_score is not None:
        try:
            calculated_score = float(explicit_score)
        except (ValueError, TypeError):
            calculated_score = sum(c["score"] for c in components.values())
    else:
        # Sum of component points
        raw_sum = sum(c["score"] for c in components.values())
        # If some inputs were unavailable, normalize to 100 scale proportionally if available_weight >= 0.50
        if available_weight >= 0.50 and available_weight < 1.0:
            calculated_score = (raw_sum / (available_weight * 100.0)) * 100.0
        else:
            calculated_score = raw_sum

    calculated_score = max(0.0, min(100.0, round(calculated_score, 1)))

    # Determine priority category
    if calculated_score >= 70.0:
        calc_category = "high"
    elif calculated_score >= 40.0:
        calc_category = "medium"
    else:
        calc_category = "low"

    category = explicit_priority if (explicit_priority in ["high", "medium", "low"] and explicit_score is not None) else calc_category

    # Human-readable summary
    summary_parts = []
    if components["confidence"]["status"] == "available":
        summary_parts.append(f"{components['confidence']['raw_value']} confidence")
    if components["frp"]["status"] == "available" and components["frp"]["score"] >= 8.0:
        summary_parts.append(f"elevated {components['frp']['raw_value']} FRP")
    if components["repeat_detections"]["status"] == "available" and components["repeat_detections"]["score"] >= 19.0:
        summary_parts.append(f"nearby cluster presence ({components['repeat_detections']['raw_value']})")
    if components["recency"]["status"] == "available" and components["recency"]["score"] >= 12.0:
        summary_parts.append(f"high recency ({components['recency']['raw_value']})")

    if summary_parts:
        explanation_summary = f"{category.upper()} priority ({calculated_score:.1f}/100) possible thermal indication driven by " + ", ".join(summary_parts) + "."
    else:
        explanation_summary = f"{category.upper()} priority ({calculated_score:.1f}/100) possible thermal indication based on available satellite telemetry."

    is_complete = all(c["status"] == "available" for c in components.values())

    return {
        "score": calculated_score,
        "category": category,
        "components": components,
        "available_weight": round(available_weight, 2),
        "is_complete": is_complete,
        "explanation_summary": explanation_summary,
    }
