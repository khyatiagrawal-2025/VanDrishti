# VanDrishti 🌲

**Detect Early. Prioritise Smarter. Protect Forests.**

VanDrishti is a software-based forest fire monitoring and response prioritisation prototype focused on Nainital, Uttarakhand, India.

The project aims to process satellite-based fire observations, visualise potential hotspots on an interactive map, assign transparent priority scores, and track response cases through a central dashboard.

## 🎯 Objectives

- Collect historical and latest available satellite fire observations.
- Filter observations using the Nainital district boundary.
- Visualise potential fire hotspots on an interactive map.
- Rank observations using a transparent, rule-based priority score.
- Track response cases through statuses such as New, Assigned, Checking, and Resolved.
- Present hotspot trends and response progress through a dashboard.

## 🛠️ Technology Stack

- **Language:** Python
- **Backend:** Django
- **Frontend:** HTML, CSS, JavaScript, Bootstrap
- **Database:** SQLite
- **Data Processing:** Pandas, GeoPandas, Shapely
- **Interactive Mapping:** Folium, Leaflet.js
- **Charts:** Chart.js
- **Satellite Data:** NASA FIRMS
- **Testing:** Django testing tools, Pytest
- **Version Control:** Git and GitHub

## 🛰️ Data Sources

- [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) — satellite-based active fire observations.
- [Forest Survey of India Fire Alerts](https://fsiforestfire.gov.in/) — a reference source for forest fire alert information.

Data availability, observation timestamps, geographic coverage, and source limitations will be documented as the project develops.

### NASA FIRMS Integration & Open-Source Architecture
For details on satellite ingestion architecture, duplicate detection handling across sensor passes, rate limits, and basemap tile usage policy compliance, see:
- [Open-Source Architectural Study & Data Sources](docs/OPEN_SOURCE_STUDY.md)

To configure live NASA FIRMS Near-Real-Time (NRT) satellite thermal anomaly ingestion:
1. Request a free 32-character Map Key at [NASA FIRMS API](https://firms.modaps.eosdis.nasa.gov/api/).
2. Copy `.env.example` to `.env` and set `FIRMS_MAP_KEY=your_key_here`.
3. Fetch live satellite observations for Nainital Forest Division:
   ```bash
   python manage.py fetch_firms_hotspots --days 1
   ```
4. For offline demonstrations without an API key, use the safe fallback flag:
   ```bash
   python manage.py fetch_firms_hotspots --sample-fallback
   ```


## ⚙️ Planned Workflow

1. Acquire available satellite observations.
2. Clean and validate the incoming data.
3. Filter observations geographically for Nainital.
4. Calculate prototype priority scores using documented rules.
5. Display observations and relevant information on a dashboard.
6. Create and track response cases for demonstration and evaluation.

## ⚠️ Important Limitations

Satellite hotspots indicate potential fire activity or thermal anomalies; they do not independently confirm a forest fire.

Priority scores are intended for prototype-level decision support, not as official fire-risk assessments or operational dispatch instructions.

Historical observations, latest available observations, and simulated response workflows will be clearly distinguished. Simulated case assignments do not represent actual field deployments or government operations.

## 🚧 Project Status

**In Development**

This repository is being developed as a software-only prototype. Features will be documented as they are implemented and tested.

## 🤝 Contributions

Contributions, suggestions, and feedback are welcome. Please open an issue to discuss significant changes before submitting a pull request.

## 📄 License

A license will be added if and when the project is released under an open-source license.