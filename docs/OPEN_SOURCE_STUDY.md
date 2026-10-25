# VanDrishti — Open-Source Architectural Study & Data Source Reference
**Nainital Forest Division, Uttarakhand, India**
*Member 1 — Backend & Integration Lead*

---

## 1. Executive Summary

To build a reliable, high-performance, and explainable forest-fire monitoring and response prototype for Nainital Forest Division, we conducted an in-depth architectural and licensing study of established open-source wildfire monitoring systems and satellite data services.

This document records the evaluation of:
1. **NASA FIRMS (Fire Information for Resource Management System)** API architecture and data ingestion standards.
2. **WorldWindEarth Wildfire Management Tool (WMT v2.0)** geospatial architecture and operational workflows.
3. **NASA FIRMS & Forest-Fire Open-Source Repositories** handling duplicate detection, confidence scales, temporal clustering, and provenance.
4. **Basemap Providers & Tile Usage Policies** to guarantee continuous, permitted, high-detail cartographic rendering without 403 rate blocks.

---

## 2. In-Depth Study of Reference Projects

### A. NASA FIRMS (Fire Information for Resource Management System)
- **Primary Portal:** [https://firms.modaps.eosdis.nasa.gov/](https://firms.modaps.eosdis.nasa.gov/)
- **API Documentation:** [https://firms.modaps.eosdis.nasa.gov/api/](https://firms.modaps.eosdis.nasa.gov/api/)
- **Data Governance & License:** Open Data Policy under NASA Earth Science Data Information System (ESDIS). Free for academic, scientific, and public domain disaster management with appropriate source attribution.

#### API Ingestion Architecture:
- **Area CSV Endpoint:**  
  `https://firms.modaps.eosdis.nasa.gov/api/area/csv/[MAP_KEY]/[SOURCE]/[AREA_COORDINATES]/[DAY_RANGE]`
- **Authentication:** 32-character hexadecimal `MAP_KEY` obtained via registered email verification. Key must be passed in the URL path.
- **Rate Limits:** 5,000 requests per 10-minute interval per `MAP_KEY`.
- **Geographic Bounding Box:** Specified as `west,south,east,north` in WGS84 decimal degrees.
  - *Nainital Forest Division Study Area:* `79.10,29.10,79.80,29.65`.
- **Satellite Sources & Sensors:**
  - `VIIRS_NOAA20_NRT`: NOAA-20 VIIRS 375m spatial resolution, ~13:30 local pass time.
  - `VIIRS_SNPP_NRT`: Suomi-NPP VIIRS 375m spatial resolution, ~13:30 local pass time (50-minute offset from NOAA-20).
  - `VIIRS_NOAA21_NRT`: NOAA-21 VIIRS 375m spatial resolution.
  - `MODIS_NRT`: Terra (~10:30 pass) and Aqua (~13:30 pass) MODIS 1km spatial resolution.
- **Sensor Column Schemas:**
  - *VIIRS:* `latitude`, `longitude`, `bright_ti4`, `scan`, `track`, `acq_date`, `acq_time`, `satellite`, `instrument`, `confidence`, `version`, `bright_ti5`, `frp`, `daynight`.
  - *MODIS:* `latitude`, `longitude`, `brightness`, `scan`, `track`, `acq_date`, `acq_time`, `satellite`, `instrument`, `confidence`, `version`, `bright_t31`, `frp`, `daynight`.
- **Confidence Normalization:**
  - VIIRS outputs categorical confidence: `'l'` (low / ~30%), `'n'` (nominal / ~65%), and `'h'` (high / ~90%).
  - MODIS outputs percentage confidence: `0` to `100%`.
- **Key Scientific Constraint:** NASA FIRMS explicitly defines hotspots as *thermal anomalies* indicating potential vegetation fires, prescribed burns, or agricultural activity. They do not constitute ground-confirmed wildfires without field verification.

---

### B. WorldWindEarth Wildfire Management Tool (WMT v2.0)
- **Repository:** [https://github.com/WorldWindEarth/wildfire](https://github.com/WorldWindEarth/wildfire)
- **License:** Apache License 2.0
- **Ecosystem:** NASA WorldWind WebGL 3D/2D geospatial client.

#### Architectural Insights Studied:
1. **Multi-Source Ingestion & Time-Slider Controls:**  
   WMT ingests multi-temporal observations and provides temporal filtering (last 6h, 12h, 24h, 48h). This avoids visual clutter from stale fires that have already been contained.
2. **Confidence-Tiered Symbol Styling:**  
   Thermal detections are categorized into visual tiers based on sensor confidence and sensor radiance rather than raw unscaled coordinates.
3. **Response Coordination Separation:**  
   WMT maintains an explicit conceptual separation between the *raw observation layer* (what satellites detected) and *incident response management* (which crew is assigned, status of containment lines, and field reconnaissance).

---

### C. Forest-Fire & FIRMS Open-Source Repositories (`pyfirms`, `active-fire-detection`)
- **Key Topics:** `https://github.com/topics/nasa-firms`, `https://github.com/topics/forest-fire`

#### Core Lessons on Data Pipelines:
1. **Duplicate Detection Across Complementary Orbits:**  
   Because NOAA-20 and Suomi-NPP fly in closely staggered orbits, a single ground fire will often trigger two detections 50 minutes apart at slightly different sub-pixel center points.  
   *VanDrishti Solution:* Instead of blindly dropping the second pass as a redundant duplicate, VanDrishti's scoring engine treats repeat detections within 3.0 km as a positive corroborating factor (`repeat_score`), boosting the priority score of high-confidence persistent anomalies.
2. **Data Provenance & Classification:**  
   Real field prototypes require strict separation between:
   - `real`: Ingested live from Near-Real-Time (NRT) satellite APIs.
   - `historical`: Archived FIRMS observations used for retrospective analysis.
   - `simulated`: Deterministic benchmark scenarios used for unit testing, training, and operational simulations.
3. **Idempotent Ingestion via Deterministic Keys:**  
   To prevent duplicate database growth during polling, observation IDs are computed deterministically from acquisition timestamp, sensor, and coordinate hash:  
   `VD-FIRMS-{YYYYMMDDHHMM}-{lat_hash}{lng_hash}`.

---

## 3. Basemap Architecture & OSM Tile Policy Compliance

### Root Cause Analysis of 403 "Access Blocked":
- Standard OpenStreetMap raster tile servers (`tile.openstreetmap.org`) strictly enforce the [OSM Foundation Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/).
- Direct browser `<img>` requests from web applications hosted on `http://127.0.0.1:8000` or `http://localhost:8000` lack customized User-Agents and possess localhost `Referer` headers. OSM's Varnish cache servers automatically block these requests with HTTP 403 / 200 containing a rendered "Access blocked" PNG tile.

### VanDrishti's Two-Tiered Permitted Basemap Solution:
1. **Client-Side Primary Road Map: CARTO Voyager**  
   - Tile URL: `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png`
   - Subdomains: `abcd`, max zoom: `20`.
   - Data Source: Powered by OpenStreetMap data.
   - Visual Quality: Shows comprehensive street names, motorways, village roads, forest trails, and settlement labels in Nainital.
   - Policy: Fully permitted for open-source / research map visualization under CARTO and OSM contributor attribution.
2. **Topographic Layer: OpenTopoMap**  
   - Tile URL: `https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png`
   - Elevation contours, mountain relief, and peak annotations in the Kumaon Himalayas.
3. **Satellite Imagery: Esri World Imagery**  
   - Tile URL: `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
   - High-resolution canopy and terrain photography.
4. **Backend Tile Proxy: `/api/map/tiles/<z>/<x>/<y>.png`**  
   - Implemented in `hotspots/views.py` (`api_map_tile_proxy`).
   - Requests upstream OSM tiles with a compliant User-Agent:  
     `VanDrishti/1.0 (Forest Fire Monitoring Prototype; Nainital Division; research@vandrishti.org)`.
   - Caches tiles in memory to comply with OSM caching rules.
   - Automatically falls back to CARTO Voyager if upstream OSM is throttled.

---

## 4. Architectural Decisions Adopted in VanDrishti

| Capability | Reference Source | VanDrishti Implementation |
|---|---|---|
| **Satellite Data Ingestion** | NASA FIRMS Area API | `fetch_firms_live_data()` in `hotspots/data_pipeline.py` with `FIRMS_MAP_KEY` support and automated schema validation. |
| **Observation Provenance** | ESDIS Guidelines | `data_nature` field with explicit choices: `'real'`, `'historical'`, `'simulated'`. |
| **Response Case Tracking** | WMT v2.0 Incident Architecture | `ResponseCase` model linked via ForeignKey to `Observation`, tracking `status`, `assigned_team`, and beat notes. |
| **Priority Scoring** | Multi-Criteria Decision Analysis (MCDA) | 5-component weighted formula (Confidence 30%, Repeat 30%, FRP 15%, Recency 15%, Forest Overlap 10%) with transparent mathematical breakdown. |
| **Geospatial Basemaps** | CARTO Voyager + OpenTopoMap + OSM Proxy | Zero 403 errors, full road and village labels, interactive layer switching in Leaflet. |
| **Offline Demonstration** | Test-Driven Best Practices | Safe `--sample-fallback` in `fetch_firms_hotspots` command when no API key is configured, avoiding mock fabrication. |
