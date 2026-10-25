# VanDrishti

**Detect Early. Prioritise Smarter. Protect Forests.**

VanDrishti is a student prototype that turns satellite thermal-anomaly detections into a ranked, explainable review list for the Nainital region of Uttarakhand, India. It is a decision-support tool for organising human review. It does not confirm fires and does not predict them.

## Event details

| | |
|---|---|
| Event | SPACE OUT: Space Innovation Ideathon 2026 |
| Theme | Theme 4 (project relevance: satellite-based forest-fire hotspot monitoring and prioritisation) |
| Track | Track 4.2 (project relevance: ranking satellite hotspots so limited field teams know where to check first) |
| Team | Team Clock it |
| Institution | GLA University, Mathura |

The presentation gives the theme and track only as "Theme 4, Track 4.2". The text in brackets describes how this project relates to them and is not an official title.

## Overview

NASA FIRMS publishes near-real-time satellite fire detections. In a busy fire season a region can show many detections at once, and not all of them are forest fires. VanDrishti sits between detection and action:

1. It retrieves FIRMS detections for a configured bounding box around Nainital.
2. It validates and stores them, keeping real, historical and simulated records separate.
3. It scores each detection with a rule-based, explainable priority score.
4. It shows the results on a map dashboard and tracks review cases.

## Problem statement

A satellite hotspot is a clue, not a confirmed fire. Farm, waste and other burning can look similar from space, and forest officials have publicly expected a large share of satellite alerts to be false (see reference 6). With limited people and time, the practical question is which hotspots to check first.

## Research question and objectives

**Research question:** Can a transparent, rule-based score help rank satellite hotspots so that a small team can decide which locations to review first?

**Objectives**

- Retrieve NASA FIRMS detections for the Nainital bounding box.
- Validate, deduplicate and store detections with clear provenance (`real`, `historical`, `simulated`).
- Rank detections with a score whose components are visible to the reviewer.
- Present the ranking on a map dashboard with filters.
- Track review cases through a simple status workflow.

Whether the ranking agrees with recorded forest-fire incidents has not been tested yet (see [Testing and validation](#testing-and-validation)).

## Main workflow

```
NASA FIRMS (VIIRS / MODIS)  ->  validate + region filter  ->  explainable score  ->  dashboard + review cases
        implemented                    implemented                 implemented             implemented

                                  Proposed, not built: weather layer, Sentinel-2 context, drone verification
```

## Key features (implemented)

- **FIRMS ingestion** through the `fetch_firms_hotspots` management command, using the FIRMS Area CSV API.
- **Validation and normalisation** of coordinates, timestamps, confidence (VIIRS `l`/`n`/`h` mapped to 30/65/90) and FRP.
- **Region filtering** to the configured Nainital bounding box (can be disabled with `--no-filter-bounds`).
- **Data provenance labels** on every observation: `real`, `historical` or `simulated`.
- **Explainable priority score (0-100)** with a per-component breakdown and a text summary.
- **Dashboard** with a Leaflet map, Chart.js charts, filters and score explanations.
- **Response cases**: create and update review cases, optionally linked to an observation.
- **JSON API** for observations and cases.
- **Offline fallback**: bundled sample CSV files so the dashboard can be demonstrated without an API key.
- **OpenStreetMap tile proxy** with in-memory caching and a Carto fallback.

## System architecture

```
                 +--------------------------+
                 |  NASA FIRMS Area CSV API |
                 +------------+-------------+
                              |  (FIRMS_MAP_KEY)
                +-------------v--------------+
                | hotspots/data_pipeline.py  |  validate, normalise, filter, store
                +-------------+--------------+
                              |
        +---------------------v----------------------+
        | SQLite: Observation, ResponseCase,         |
        |         TelemetrySync                      |
        +---------------------+----------------------+
                              |
                +-------------v--------------+
                | hotspots/scoring.py        |  explainable priority score
                +-------------+--------------+
                              |
                +-------------v--------------+
                | hotspots/views.py          |  dashboard pages + JSON API
                +-------------+--------------+
                              |
                +-------------v--------------+
                | Templates + dashboard.js   |  Leaflet map, Chart.js, filters
                +----------------------------+
```

The backend is a single Django app named `hotspots`. Pages are server-rendered Django templates, and the dashboard script fetches data from the JSON API.

## Repository structure

```
VanDrishti/
├── manage.py
├── requirements.txt
├── .env.example
├── config/                         Django project settings and URL routing
│   ├── settings.py
│   └── urls.py
├── docs/
│   └── OPEN_SOURCE_STUDY.md        Study of FIRMS ingestion and basemap policies
└── hotspots/                       Main Django app
    ├── models.py                   Observation, ResponseCase, TelemetrySync
    ├── scoring.py                  Explainable priority scoring
    ├── data_pipeline.py            Validation, CSV import, FIRMS fetch
    ├── views.py                    Dashboard views, JSON API, tile proxy
    ├── urls.py
    ├── admin.py
    ├── tests.py
    ├── data/
    │   ├── simulated_benchmark_hotspots.csv
    │   └── historical_firms_nainital_sample.csv
    ├── management/commands/
    │   ├── fetch_firms_hotspots.py
    │   ├── load_observations.py
    │   └── seed_nainital_observations.py
    ├── migrations/
    ├── static/hotspots/            css/style.css, js/dashboard.js
    └── templates/hotspots/         base.html, dashboard.html
```

## Technology stack

| Layer | Technology |
|---|---|
| Language | Python |
| Backend | Django 6.1.2 |
| Database | SQLite |
| Frontend | Django templates, HTML, CSS, JavaScript |
| UI libraries (loaded from CDN) | Bootstrap 5.3.3, Bootstrap Icons 1.11.3 |
| Map | Leaflet 1.9.4 with OpenStreetMap raster tiles (via a local proxy) |
| Charts | Chart.js 4.4.1 |
| Data source | NASA FIRMS Area CSV API |
| Testing | Django test framework |

`requirements.txt` lists only Django and its dependencies (`asgiref`, `sqlparse`, `tzdata`). The project does not use Pandas, GeoPandas, Folium or React. The dashboard loads its frontend libraries from CDNs, so it needs an internet connection in the browser.

## NASA FIRMS data source and geographic scope

- **Source:** [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/), Area CSV API.
- **Default stream:** `VIIRS_NOAA20_NRT`. The command also accepts `VIIRS_SNPP_NRT`, `VIIRS_NOAA21_NRT` and `MODIS_NRT`.
- **Time window:** 1 to 5 days per request (the code clamps values to this range).
- **VIIRS pixel size:** about 375 m, so a detection is an area, not an exact point.

**Configured bounding box**

| West | South | East | North |
|---:|---:|---:|---:|
| 79.10 | 29.10 | 79.80 | 29.65 |

Each observation carries a `data_nature` label:

| Label | Meaning |
|---|---|
| `real` | Retrieved from the FIRMS API by `fetch_firms_hotspots` |
| `historical` | Loaded from the bundled historical sample CSV |
| `simulated` | Synthetic benchmark records for demonstration |

The bundled CSV files are small samples (6 rows each) for demonstration. Their provenance has not been verified against the FIRMS archive, so treat them as illustrative and not as evidence of past fires.

## Risk-prioritisation methodology

The implemented weights match the proposed weights in the presentation.

| Factor | Weight | How it is computed in `scoring.py` |
|---|---:|---|
| Satellite confidence | 30% | Confidence (0-100) scaled linearly to 30 points |
| Repeat detections nearby | 30% | Counts other stored observations within 3.5 km: 2 or more = 30, 1 = 19.5, 0 = 7.5 |
| Fire Radiative Power (FRP) | 15% | FRP divided by a 20 MW ceiling, capped at 1, scaled to 15 points |
| Recency | 15% | Step function by age: up to 2 h = 15, 6 h = 12, 12 h = 9, 24 h = 6, 48 h = 3, older = 1 |
| Inside forest area | 10% | Inside the bounding box and matching a keyword list of known forest ranges = 10, inside the box only = 7, outside = 2 |

**Categories:** score of 70 or more is `high`, 40 to 69.9 is `medium`, below 40 is `low`.

**Missing inputs:** if some components are unavailable and at least 50% of the total weight is available, the score is rescaled to the available weight. The API response reports which components were available.

Points to be aware of:

- The forest-area factor is a heuristic. It uses the bounding box and a keyword list on location and range names. It does not use a forest-cover polygon layer.
- The repeat-detection factor counts any other stored observation within 3.5 km, regardless of when it was detected. It is a spatial cluster count and not a measure of persistence over time.
- The 20 MW FRP ceiling, the recency steps and the repeat-detection point values are prototype choices. They have not been calibrated against recorded incidents.
- The score ranks detections for review. It is not an official fire-risk assessment.

## Installation and setup

Requirements: Python 3.12 or newer (the project was verified on Python 3.12.3) and Git.

**Windows (PowerShell)**

```powershell
git clone https://github.com/khyatiagrawal-2025/VanDrishti.git
cd VanDrishti
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
git clone https://github.com/khyatiagrawal-2025/VanDrishti.git
cd VanDrishti
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

If PowerShell blocks the activation script, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for the current session first.

## Environment-variable configuration

`config/settings.py` loads a root-level `.env` file with a small built-in loader. Values already set in the shell take priority.

| Variable | Used by the code | Purpose |
|---|---|---|
| `FIRMS_MAP_KEY` | Yes | Your NASA FIRMS MAP_KEY. Request a free key at <https://firms.modaps.eosdis.nasa.gov/api/> |
| `FIRMS_DEFAULT_SOURCE` | Read into settings | Default FIRMS stream name (`VIIRS_NOAA20_NRT`) |
| `NAINITAL_BBOX` | Read into settings | Bounding box as `west,south,east,north` |
| `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS` | No | Listed in `.env.example`, but `settings.py` currently hard-codes these values |

Set your key in `.env`:

```
FIRMS_MAP_KEY=your_32_character_key_here
```

Never commit `.env`. It is already listed in `.gitignore`. The `fetch_firms_hotspots` command takes its source and bounding box from its own `--source` and `--bbox` arguments, whose defaults match the values above.

## Database migrations and execution commands

```bash
python manage.py migrate
python manage.py load_observations     # loads the simulated benchmark CSV (6 rows)
python manage.py runserver
```

Open <http://127.0.0.1:8000/>.

Other available commands:

```bash
python manage.py seed_nainital_observations          # seed simulated benchmark observations
python manage.py seed_nainital_observations --clear  # remove all observations and telemetry status
python manage.py load_observations --csv PATH --data-type historical --dry-run
python manage.py createsuperuser                     # optional, for /admin/
```

`load_observations` options: `--csv`, `--data-type {simulated,historical,real}`, `--clear`, `--dry-run`, `--filter-bounds`.

## Live hotspot data retrieval

With `FIRMS_MAP_KEY` set:

```bash
python manage.py fetch_firms_hotspots --days 1
```

Options:

| Option | Description |
|---|---|
| `--key` | MAP_KEY override (defaults to `FIRMS_MAP_KEY`) |
| `--source` | `VIIRS_NOAA20_NRT` (default), `VIIRS_SNPP_NRT`, `VIIRS_NOAA21_NRT`, `MODIS_NRT` |
| `--days` | 1 to 5 (default 1) |
| `--bbox` | `min_lng,min_lat,max_lng,max_lat` (default is the Nainital box above) |
| `--dry-run` | Validate and score without writing to the database |
| `--no-filter-bounds` | Keep detections outside the Nainital box |
| `--sample-fallback` | If the live fetch fails, load the bundled historical sample instead |

Retrieved records are stored as `real`. The sample fallback stores records as `historical`. A live request returning zero detections is a valid result: it means FIRMS reported no thermal anomalies in the box for that window.

For an offline demonstration without a key:

```bash
python manage.py fetch_firms_hotspots --sample-fallback
```

The dashboard also has a refresh action (`POST /api/refresh/`) that runs a one-day live fetch when a key is configured.

## Dashboard and API usage

**Pages**

| Route | Description |
|---|---|
| `/` | Dashboard: map, charts, ranked observations |
| `/hotspots/` | Same dashboard with the hotspots and cases view active |
| `/admin/` | Django admin |

**JSON API**

| Endpoint | Methods | Description |
|---|---|---|
| `/api/observations/` | GET | List observations with scores. Filters: `priority`, `status`, `data_nature`, `min_confidence`, `search` |
| `/api/observations/<observation_id>/` | GET, POST, PATCH | Detail with score breakdown; update status or notes |
| `/api/cases/` | GET, POST | List or create response cases (GET accepts a status filter) |
| `/api/cases/<case_id>/` | GET, POST, PATCH | Case detail and updates |
| `/api/refresh/` | POST | Trigger a telemetry sync |
| `/api/map/tiles/<z>/<x>/<y>.png` | GET | Basemap tile proxy |

Example:

```bash
curl "http://127.0.0.1:8000/api/observations/?priority=high&data_nature=real"
```

**Response cases** use the statuses `new`, `assigned`, `under-review`, `verified` and `closed`, with an optional `assigned_team` text field. This is a record-keeping workflow. Any team assignments you see in the bundled data are simulated and do not represent real field deployments.

## Testing and validation

```bash
python manage.py check
python manage.py test
```

Results from running these on the current repository (Python 3.12.3, Django 6.1.2, in a sandbox with no outbound internet access):

| Check | Result |
|---|---|
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| `python manage.py test` | 55 tests ran, 54 passed, 1 failed |

The one failure is `test_api_map_tile_proxy_valid`. It requests a real tile from OpenStreetMap, so it fails when the machine has no internet access, and it should pass on a connected machine. This has not been re-run with internet access, so treat it as unconfirmed until you run it yourself.

The tests cover models, scoring, the data pipeline, dashboard views, the API endpoints, response cases, management commands and mocked FIRMS responses. They check that the code behaves as written. They do not measure how well the ranking identifies real forest fires.

## Actual results

No accuracy, precision, recall or response-time results have been measured. The planned validation is to run archived FIRMS data for chosen Nainital seasons through the pipeline and compare the top-ranked detections with recorded forest-fire incidents. Until that is done, this project makes no performance claim.

## Limitations and safety boundaries

- A satellite hotspot is a thermal-anomaly detection, not a confirmed forest fire.
- VanDrishti does not predict future fires.
- Rankings help organise human review. A high score does not prove a fire exists, and a low score does not rule one out.
- VIIRS pixels are about 375 m, and clouds, smoke and satellite revisit times can hide fires.
- The scoring weights are proposed prototype values and have not been validated against ground truth.
- The forest-area factor is a bounding-box and keyword heuristic, not a forest-cover layer.
- The bundled `simulated` and `historical` CSV files are small demonstration samples.
- Drone verification, a weather layer and Sentinel-2 context are not implemented.
- The API endpoints that change data have no authentication and are CSRF-exempt, and `settings.py` runs with `DEBUG = True` and a hard-coded secret key. The project is for local development and demonstration and is not ready for public deployment.
- VanDrishti is a student prototype and decision-support tool. It is not an operational emergency-response system and has no official endorsement, partnership or deployment.

## Future scope

The following items come from the project presentation and are proposals only.

- Backtest the ranking on past seasons against recorded incidents and report results honestly.
- Add a weather layer.
- Add Sentinel-2 imagery for after-the-fact checking of hotspots (not integrated today).
- Use more than one satellite stream. Suomi NPP is expected to end service on or before October 2026, so the pipeline should rely on NOAA-20 and NOAA-21 data.
- Simulated drone mission planning for top-ranked hotspots, followed by a pilot only with proper permissions.
- Extend to more districts and add alerts for field teams.

## References and data-source credits

**Data sources used by the application**

- NASA FIRMS: <https://firms.modaps.eosdis.nasa.gov/>
- NASA Earthdata, VIIRS I-band 375 m active fire data: <https://www.earthdata.nasa.gov/data/instruments/viirs/viirs-i-band-375-m-active-fire-data>
- NASA Earthdata, near-real-time data and citation guidance: <https://www.earthdata.nasa.gov/earth-observation-data/near-real-time/citation>
- OpenStreetMap contributors (basemap tiles), with Carto as a fallback tile provider.

**Background research references (not used as application data)**

- Tribune India, FSI report on Uttarakhand fire counts and risk zones: <https://www.tribuneindia.com/news/uttarakhand/uttarakhand-sees-74-rise-in-forest-fires-worst-hit-in-country-2>
- ETV Bharat / PTI, Climate Trends district analysis (29 Apr 2024): <https://www.etvbharat.com/en/!state/manifold-increase-in-forest-fires-in-uttarakhand-since-2023-say-experts-enn24042906211>
- ETV Bharat, false-alert expectation (23 Mar 2025): <https://www.etvbharat.com/amp/en/!state/uttarakhand-forest-fires-decrease-in-first-month-of-fire-season-enn25032301534>
- FAO, Global Forest Resources Assessment 2020: <https://www.fao.org/interactive/forest-resources-assessment/2020/en>

**Possible future source (not integrated)**

- Copernicus Browser (Sentinel-2 imagery): <https://dataspace.copernicus.eu/browser/>

Statistics quoted in the presentation come from the news sources above. Images in the presentation are illustrations and not real records.

## Team members and responsibilities

**Team Clock it, GLA University, Mathura**

| Member | Role |
|---|---|
| Khyati Agrawal | Team Leader & Backend and Integration Lead |
| Nishank Chaudhary | Satellite Data & Geospatial Processing Lead |
| Anshika Agrawal | Frontend Lead |

## Acknowledgement

This project was prepared for SPACE OUT: Space Innovation Ideathon 2026 at GLA University, Mathura. We thank NASA FIRMS and NASA Earthdata for open access to fire-detection data, and the OpenStreetMap contributors for basemap data.