/* ==========================================================================
   VanDrishti — Forest Intelligence Platform
   Nainital Forest Division, Uttarakhand, India
   Core Frontend Orchestration Script
   ========================================================================== */

(function () {
    "use strict";

    /* ----------------------------------------------------------------------
       Utility & DOM Helpers
       ---------------------------------------------------------------------- */
    function qs(selector, parent) {
        return (parent || document).querySelector(selector);
    }

    function qsa(selector, parent) {
        return Array.prototype.slice.call((parent || document).querySelectorAll(selector));
    }

    function normalise(text) {
        return (text || "").toString().toLowerCase().trim();
    }

    function onReady(callback) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", callback);
        } else {
            callback();
        }
    }

    // Global application state
    var appState = {
        observations: [],
        timelineData: [],
        scoreBinsData: {},
        visibleObservations: [],
        map: null,
        markersLayer: null,
        markerMap: {},
        boundaryLayer: null,
        currentBasemap: null,
        basemapLayers: {},
        chartInstance: null,
        timelineChartInstance: null,
        scoreDistChartInstance: null,
        selectedObservation: null,
        sortColumn: "date",
        sortDirection: "desc",
        currentPage: 1,
        pageSize: 10,
    };

    /* ----------------------------------------------------------------------
       1. Data Parsing & Initialization
       ---------------------------------------------------------------------- */
    function parsePayload(scriptId, fallback) {
        var el = qs(scriptId);
        if (!el) return fallback;
        try {
            var raw = el.textContent || "";
            return JSON.parse(raw);
        } catch (err) {
            console.warn("[VanDrishti] Could not parse payload for " + scriptId + ":", err);
            return fallback;
        }
    }

    function initData() {
        appState.observations = parsePayload("#vdObservationsData", []);
        appState.timelineData = parsePayload("#vdTimelineData", []);
        appState.scoreBinsData = parsePayload("#vdScoreBinsData", {});
        appState.visibleObservations = appState.observations.slice();
    }

    /* ----------------------------------------------------------------------
       2. Leaflet Geospatial Visualization (Nainital Center)
       ---------------------------------------------------------------------- */
    function initGeospatialMap() {
        var mapContainer = qs("#dashboardMap");
        if (!mapContainer || typeof L === "undefined") {
            var spinner = qs("#mapLoadingSpinner");
            if (spinner) spinner.classList.add("loaded");
            return;
        }

        // Nainital Division Center: 29.3803° N, 79.4636° E
        var nainitalCenter = [29.3803, 79.4636];
        var defaultZoom = 13;

        try {
            appState.map = L.map("dashboardMap", {
                center: nainitalCenter,
                zoom: defaultZoom,
                minZoom: 8,
                maxZoom: 19,
                zoomControl: true,
                attributionControl: true,
            });

            // 1. Primary Road & Settlement Basemap: Standard OpenStreetMap
            // Global coverage providing full street-level details, road names, settlements, village paths in Nainital
            var osmLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
                minZoom: 8,
                maxZoom: 19,
                crossOrigin: true,
            });

            // 2. Topographic Map: OpenTopoMap with elevation shading, peaks and contour lines
            var topoLayer = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
                attribution: 'Map data: &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org" target="_blank" rel="noopener">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org" target="_blank" rel="noopener">OpenTopoMap</a>',
                subdomains: "abc",
                minZoom: 8,
                maxNativeZoom: 17,
                maxZoom: 19,
            });

            // 3. High-Resolution Aerial Satellite Imagery: Esri World Imagery
            // Visualizing the mountain terrain, forest canopy, and pine slopes across Nainital Division
            var satelliteLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
                attribution: 'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a> &bull; Source: Esri, Maxar, Earthstar Geographics',
                minZoom: 8,
                maxZoom: 19,
            });

            appState.basemapLayers = {
                osm: osmLayer,
                topo: topoLayer,
                voyager: satelliteLayer, // Mapped to satellite imagery to preserve existing button ID
            };

            // Set OpenStreetMap as the default active basemap
            appState.currentBasemap = appState.basemapLayers.osm;
            appState.currentBasemap.addTo(appState.map);

            // Tile error state management
            var tileErrorBanner = qs("#mapTileErrorBanner");
            var tileErrorText = qs("#mapTileErrorText");
            var btnSwitchBackup = qs("#btnSwitchBackupLayer");
            var consecutiveTileErrors = 0;
            var tileErrorTimeout = null;

            function showTileError(msg) {
                if (tileErrorBanner) {
                    if (tileErrorText) tileErrorText.textContent = msg;
                    tileErrorBanner.classList.remove("d-none");
                }
            }

            function clearTileError() {
                if (tileErrorBanner) {
                    tileErrorBanner.classList.add("d-none");
                }
                consecutiveTileErrors = 0;
            }

            function attachTileErrorHandling(layer, layerName) {
                layer.on("tileerror", function () {
                    consecutiveTileErrors++;
                    if (consecutiveTileErrors >= 4) {
                        showTileError("Map tiles for " + layerName + " failed to load. Please check your network connection.");
                    }
                    clearTimeout(tileErrorTimeout);
                    tileErrorTimeout = setTimeout(function () {
                        consecutiveTileErrors = 0;
                    }, 4000);
                });

                layer.on("tileload", function () {
                    if (consecutiveTileErrors > 0) consecutiveTileErrors--;
                    if (consecutiveTileErrors === 0) clearTileError();
                });
            }

            attachTileErrorHandling(osmLayer, "OpenStreetMap");
            attachTileErrorHandling(topoLayer, "OpenTopoMap");
            attachTileErrorHandling(satelliteLayer, "Satellite Imagery");

            if (btnSwitchBackup) {
                btnSwitchBackup.addEventListener("click", function () {
                    if (appState.currentBasemap === appState.basemapLayers.osm) {
                        switchBasemap(appState.basemapLayers.voyager, qs("#btnLayerVoyager"));
                    } else {
                        switchBasemap(appState.basemapLayers.osm, qs("#btnLayerOSM"));
                    }
                    clearTileError();
                });
            }

            // Hide loading spinner
            var spinner = qs("#mapLoadingSpinner");
            if (spinner) {
                setTimeout(function () {
                    spinner.classList.add("loaded");
                }, 300);
            }

            // Approximate Nainital Forest Division boundary polygon
            var divisionBoundaryCoords = [
                [29.475, 79.370],
                [29.485, 79.450],
                [29.470, 79.560],
                [29.440, 79.610],
                [29.360, 79.600],
                [29.310, 79.540],
                [29.300, 79.460],
                [29.320, 79.380],
                [29.390, 79.350],
                [29.475, 79.370],
            ];

            appState.boundaryLayer = L.polygon(divisionBoundaryCoords, {
                color: "#1E6B45",
                weight: 2,
                dashArray: "6, 6",
                fillColor: "#39A875",
                fillOpacity: 0.05,
            }).addTo(appState.map);

            // Layer for Observation Anomaly Markers
            appState.markersLayer = L.layerGroup().addTo(appState.map);

            // Initial Marker Render
            renderMapMarkers(appState.visibleObservations);

            // Setup Mouse Coordinates HUD
            var coordsText = qs("#mapCoordsText");
            if (coordsText) {
                appState.map.on("mousemove", function (e) {
                    var lat = e.latlng.lat.toFixed(4);
                    var lng = e.latlng.lng.toFixed(4);
                    coordsText.textContent = lat + "° N, " + lng + "° E · Nainital Division";
                });
            }

            // Basemap Switcher Buttons
            var btnVoyager = qs("#btnLayerVoyager");
            var btnTopo = qs("#btnLayerTopo");
            var btnOSM = qs("#btnLayerOSM");

            function switchBasemap(newLayer, activeBtn) {
                if (!newLayer || appState.currentBasemap === newLayer) return;

                clearTileError();

                if (appState.currentBasemap && appState.map.hasLayer(appState.currentBasemap)) {
                    appState.map.removeLayer(appState.currentBasemap);
                }

                newLayer.addTo(appState.map);
                if (typeof newLayer.bringToBack === "function") {
                    newLayer.bringToBack();
                }
                appState.currentBasemap = newLayer;

                if (appState.boundaryLayer && typeof appState.boundaryLayer.bringToFront === "function") {
                    appState.boundaryLayer.bringToFront();
                }

                [btnVoyager, btnTopo, btnOSM].forEach(function (btn) {
                    if (btn) btn.classList.remove("active");
                });
                if (activeBtn) activeBtn.classList.add("active");
            }

            if (btnVoyager) {
                btnVoyager.addEventListener("click", function () {
                    switchBasemap(appState.basemapLayers.voyager, btnVoyager);
                });
            }
            if (btnTopo) {
                btnTopo.addEventListener("click", function () {
                    switchBasemap(appState.basemapLayers.topo, btnTopo);
                });
            }
            if (btnOSM) {
                btnOSM.addEventListener("click", function () {
                    switchBasemap(appState.basemapLayers.osm, btnOSM);
                });
            }

            // Fit Bounds Button
            var btnFit = qs("#btnFitBounds");
            if (btnFit) {
                btnFit.addEventListener("click", function () {
                    fitMapToVisibleMarkers();
                });
            }

            // Reset Map View Button
            var btnReset = qs("#btnResetMapView");
            if (btnReset) {
                btnReset.addEventListener("click", function () {
                    appState.map.flyTo(nainitalCenter, defaultZoom, {
                        duration: 0.8,
                        easeLinearity: 0.25,
                    });
                });
            }

            window.addEventListener("resize", function () {
                if (appState.map) {
                    appState.map.invalidateSize();
                }
            });
        } catch (err) {
            console.error("[VanDrishti] Error initializing map:", err);
            var mapEmptyOverlay = qs("#mapEmptyOverlay");
            if (mapEmptyOverlay) mapEmptyOverlay.classList.remove("d-none");
        }
    }

    function createCustomMarkerIcon(priority) {
        var pClass = "marker-medium";
        if (priority === "high") pClass = "marker-high";
        else if (priority === "low") pClass = "marker-low";

        var html = '<div class="vd-marker-pulse ' + pClass + '">' +
                   '<div class="vd-marker-ring"></div>' +
                   '<div class="vd-marker-core"></div>' +
                   '</div>';

        return L.divIcon({
            html: html,
            className: "vd-leaflet-custom-marker",
            iconSize: [22, 22],
            iconAnchor: [11, 11],
            popupAnchor: [0, -11],
        });
    }

    function formatDateDisplay(isoDate) {
        if (!isoDate) return "";
        try {
            // Parse "YYYY-MM-DD" safely without timezone shift by treating it as local
            var parts = isoDate.split("-");
            if (parts.length === 3) {
                var d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
                return d.toLocaleDateString("en-IN", { year: "numeric", month: "short", day: "numeric" });
            }
        } catch (e) {}
        return escapeHtml(isoDate);
    }

    function escapeHtml(text) {
        if (!text) return "";
        return text
            .toString()
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function renderMapMarkers(observationsToRender) {
        if (!appState.map || !appState.markersLayer) return;

        appState.markersLayer.clearLayers();
        appState.markerMap = {};

        var obsList = observationsToRender || [];
        var badge = qs("#mapAnomalyCountBadge");
        var emptyOverlay = qs("#mapEmptyOverlay");
        var emptyMsg = qs("#mapEmptyMessage");
        var filteredBadge = qs("#mapFilteredNoticeBadge");

        if (badge) {
            badge.textContent = obsList.length + " Anomaly Marker" + (obsList.length !== 1 ? "s" : "");
        }

        var isFiltered = obsList.length !== appState.observations.length;
        if (filteredBadge) {
            filteredBadge.classList.toggle("d-none", !isFiltered);
        }

        if (obsList.length === 0) {
            if (emptyOverlay) {
                emptyOverlay.classList.remove("d-none");
                if (emptyMsg) {
                    emptyMsg.textContent = isFiltered
                        ? "No satellite thermal anomalies match the current filter selection."
                        : "Zero thermal anomalies recorded in the database for Nainital Forest Division.";
                }
            }
            return;
        }

        if (emptyOverlay) emptyOverlay.classList.add("d-none");

        obsList.forEach(function (obs) {
            var lat = parseFloat(obs.lat);
            var lng = parseFloat(obs.lng);

            // Strict coordinate bounds validation
            if (isNaN(lat) || isNaN(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
                return;
            }

            var latLng = [lat, lng];
            var icon = createCustomMarkerIcon(obs.priority);
            var marker = L.marker(latLng, { icon: icon });

            var priorityLabel = (obs.priority || "medium").toUpperCase();
            var scoreText = obs.priority_score !== undefined ? obs.priority_score + "/100" : "--";
            var confText = obs.confidence ? obs.confidence + "%" : "Unavailable";
            var frpText = obs.frp_mw ? obs.frp_mw + " MW" : "Unavailable";
            var natureTag = obs.data_nature === "simulated" ? "Simulated Demo" : (obs.data_nature === "historical" ? "Historical Record" : "Real Telemetry");

            var popupContent = '<div class="vd-popup-card">' +
                '<div class="vd-popup-header d-flex justify-content-between align-items-center mb-1">' +
                    '<span class="vd-popup-id font-monospace fw-bold">' + escapeHtml(obs.id) + '</span>' +
                    '<span class="badge vd-badge-priority badge-' + (obs.priority || 'medium') + '">' + priorityLabel + ' (' + scoreText + ')</span>' +
                '</div>' +
                '<div class="vd-popup-location fw-semibold text-dark mb-1">' + escapeHtml(obs.location || 'Nainital Beat') + '</div>' +
                '<div class="vd-popup-meta small text-muted mb-2">' +
                    '<div><i class="bi bi-geo-alt me-1"></i>' + lat.toFixed(4) + '° N, ' + lng.toFixed(4) + '° E</div>' +
                    '<div><i class="bi bi-clock me-1"></i>' + escapeHtml(obs.detected_at || '') + '</div>' +
                    '<div><i class="bi bi-satellite me-1"></i>' + escapeHtml(obs.source || 'VIIRS') + ' &bull; ' + confText + ' conf</div>' +
                    '<div><i class="bi bi-fire me-1"></i>FRP: ' + frpText + ' &bull; Status: ' + escapeHtml(obs.status || 'new') + '</div>' +
                    '<div class="mt-1"><span class="badge bg-light text-secondary border">' + natureTag + '</span></div>' +
                '</div>' +
                '<button type="button" class="btn btn-sm btn-success w-100 vd-popup-btn" data-obs-id="' + escapeHtml(obs.id) + '">' +
                    '<i class="bi bi-card-text me-1"></i>Inspect Score &amp; Details' +
                '</button>' +
            '</div>';

            marker.bindPopup(popupContent, { maxWidth: 280 });

            marker.on("click", function () {
                openObservationDrawer(obs);
            });

            marker.on("popupopen", function () {
                var btn = qs('.vd-popup-btn[data-obs-id="' + obs.id + '"]');
                if (btn) {
                    btn.addEventListener("click", function () {
                        openObservationDrawer(obs);
                    });
                }
            });

            appState.markersLayer.addLayer(marker);
            appState.markerMap[obs.id] = marker;
        });
    }

    function fitMapToVisibleMarkers() {
        if (!appState.map || !appState.markersLayer) return;

        var bounds = L.latLngBounds();
        var hasValidCoords = false;

        appState.visibleObservations.forEach(function (obs) {
            var lat = parseFloat(obs.lat);
            var lng = parseFloat(obs.lng);
            if (!isNaN(lat) && !isNaN(lng)) {
                bounds.extend([lat, lng]);
                hasValidCoords = true;
            }
        });

        if (hasValidCoords && bounds.isValid()) {
            appState.map.fitBounds(bounds, {
                padding: [40, 40],
                maxZoom: 14,
            });
        } else {
            appState.map.flyTo([29.3803, 79.4636], 12);
        }
    }

    /* ----------------------------------------------------------------------
       3. Charts & Analytics (Chart.js)
       ---------------------------------------------------------------------- */
    function initPriorityChart() {
        var canvas = qs("#priorityChart");
        if (!canvas || typeof Chart === "undefined") return;

        var totalCount = appState.observations.length;
        var highCount = 0;
        var mediumCount = 0;
        var lowCount = 0;

        appState.observations.forEach(function (obs) {
            if (obs.priority === "high") highCount++;
            else if (obs.priority === "medium") mediumCount++;
            else if (obs.priority === "low") lowCount++;
        });

        // Update count badges
        var elHigh = qs("#countHigh");
        var elMedium = qs("#countMedium");
        var elLow = qs("#countLow");
        if (elHigh) elHigh.textContent = highCount;
        if (elMedium) elMedium.textContent = mediumCount;
        if (elLow) elLow.textContent = lowCount;

        var emptyBox = qs("#priorityChartEmpty");
        if (totalCount === 0) {
            if (emptyBox) emptyBox.classList.remove("d-none");
            canvas.style.display = "none";
            return;
        }

        if (emptyBox) emptyBox.classList.add("d-none");
        canvas.style.display = "block";

        if (appState.chartInstance) {
            appState.chartInstance.destroy();
        }

        var ctx = canvas.getContext("2d");
        appState.chartInstance = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: ["High Priority", "Medium Priority", "Low Priority"],
                datasets: [
                    {
                        data: [highCount, mediumCount, lowCount],
                        backgroundColor: ["#D95B46", "#E8A33E", "#39A875"],
                        borderColor: ["#FFFFFF", "#FFFFFF", "#FFFFFF"],
                        borderWidth: 3,
                        hoverOffset: 6,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "70%",
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#102F24",
                        titleFont: { family: "Inter", size: 12, weight: "bold" },
                        bodyFont: { family: "Inter", size: 12 },
                        padding: 10,
                        cornerRadius: 8,
                        callbacks: {
                            label: function (context) {
                                var value = context.raw || 0;
                                var pct = totalCount > 0 ? Math.round((value / totalCount) * 100) : 0;
                                return " " + context.label + ": " + value + " (" + pct + "%)";
                            },
                        },
                    },
                },
                animation: {
                    animateRotate: true,
                    animateScale: true,
                    duration: 600,
                },
            },
        });
    }

    function initTimelineChart() {
        var canvas = qs("#timelineChart");
        if (!canvas || typeof Chart === "undefined") return;

        var timeline = appState.timelineData || [];
        var emptyBox = qs("#timelineChartEmpty");

        if (!timeline || timeline.length === 0) {
            if (emptyBox) emptyBox.classList.remove("d-none");
            canvas.style.display = "none";
            return;
        }

        if (emptyBox) emptyBox.classList.add("d-none");
        canvas.style.display = "block";

        if (appState.timelineChartInstance) {
            appState.timelineChartInstance.destroy();
        }

        var labels = timeline.map(function (item) { return item.date; });
        var highSeries = timeline.map(function (item) { return item.high || 0; });
        var medSeries = timeline.map(function (item) { return item.medium || 0; });
        var lowSeries = timeline.map(function (item) { return item.low || 0; });

        var ctx = canvas.getContext("2d");
        appState.timelineChartInstance = new Chart(ctx, {
            type: "bar",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "High Priority",
                        data: highSeries,
                        backgroundColor: "#D95B46",
                        borderRadius: 4,
                        stack: "Stack 0",
                    },
                    {
                        label: "Medium Priority",
                        data: medSeries,
                        backgroundColor: "#E8A33E",
                        borderRadius: 4,
                        stack: "Stack 0",
                    },
                    {
                        label: "Low Priority",
                        data: lowSeries,
                        backgroundColor: "#39A875",
                        borderRadius: 4,
                        stack: "Stack 0",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { font: { family: "Inter", size: 11 } },
                    },
                    y: {
                        beginAtZero: true,
                        ticks: {
                            precision: 0,
                            font: { family: "Inter", size: 11 },
                        },
                        grid: { color: "#EAF1E8" },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#102F24",
                        titleFont: { family: "Inter", size: 12, weight: "bold" },
                        bodyFont: { family: "Inter", size: 12 },
                        padding: 10,
                        cornerRadius: 8,
                    },
                },
            },
        });
    }

    function initScoreDistChart() {
        var canvas = qs("#scoreDistChart");
        if (!canvas || typeof Chart === "undefined") return;

        var bins = appState.scoreBinsData || {};
        var total = appState.observations.length;
        var emptyBox = qs("#scoreDistChartEmpty");

        if (total === 0) {
            if (emptyBox) emptyBox.classList.remove("d-none");
            canvas.style.display = "none";
            return;
        }

        if (emptyBox) emptyBox.classList.add("d-none");
        canvas.style.display = "block";

        if (appState.scoreDistChartInstance) {
            appState.scoreDistChartInstance.destroy();
        }

        var highTier = bins.high_tier || 0;
        var midTier = bins.mid_tier || 0;
        var lowTier = bins.low_tier || 0;

        var ctx = canvas.getContext("2d");
        appState.scoreDistChartInstance = new Chart(ctx, {
            type: "bar",
            data: {
                labels: ["Low (<40)", "Mid (40-69)", "High (≥70)"],
                datasets: [
                    {
                        data: [lowTier, midTier, highTier],
                        backgroundColor: ["#39A875", "#E8A33E", "#D95B46"],
                        borderRadius: 6,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { font: { family: "Inter", size: 10 } },
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { precision: 0, font: { family: "Inter", size: 10 } },
                        grid: { color: "#EAF1E8" },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#102F24",
                        titleFont: { family: "Inter", size: 12, weight: "bold" },
                        bodyFont: { family: "Inter", size: 12 },
                    },
                },
            },
        });
    }

    /* ----------------------------------------------------------------------
       4. Observation Details Slide-Out Drawer
       ---------------------------------------------------------------------- */
    function openObservationDrawer(obs) {
        if (!obs) return;
        appState.selectedObservation = obs;

        var drawer = qs("#vdObservationDrawer");
        var backdrop = qs("#vdDrawerBackdrop");
        if (!drawer || !backdrop) return;

        // Drawer Header elements
        var title = qs("#drawerTitle");
        var priorityBadge = qs("#drawerPriorityBadge");
        var scoreBadge = qs("#drawerScoreBadge");
        var statusBadge = qs("#drawerStatusBadge");
        var dataNatureBadge = qs("#drawerDataNatureBadge");

        if (title) title.textContent = obs.id || "Observation Details";

        var p = (obs.priority || "medium").toLowerCase();
        if (priorityBadge) {
            priorityBadge.className = "vd-drawer-badge badge-" + p;
            priorityBadge.textContent = p.toUpperCase() + " PRIORITY";
        }

        var scoreVal = obs.priority_score !== undefined ? obs.priority_score : "--";
        if (scoreBadge) {
            scoreBadge.textContent = "Score: " + scoreVal + "/100";
        }

        if (statusBadge) {
            statusBadge.className = "vd-drawer-status-badge status-" + (obs.status || "new");
            statusBadge.textContent = (obs.status || "New").replace("-", " ").toUpperCase();
        }

        if (dataNatureBadge) {
            var nature = obs.data_nature || "simulated";
            if (nature === "simulated") {
                dataNatureBadge.className = "badge bg-warning-subtle text-warning-emphasis border border-warning-subtle";
                dataNatureBadge.textContent = "Simulated Benchmark";
            } else if (nature === "historical") {
                dataNatureBadge.className = "badge bg-info-subtle text-info-emphasis border border-info-subtle";
                dataNatureBadge.textContent = "Historical Record";
            } else {
                dataNatureBadge.className = "badge bg-success-subtle text-success-emphasis border border-success-subtle";
                dataNatureBadge.textContent = "Real FIRMS Feed";
            }
        }

        // Location & Coordinates
        var locName = qs("#drawerLocationName");
        var forestRange = qs("#drawerForestRange");
        var coords = qs("#drawerCoordinates");
        if (locName) locName.textContent = obs.location || "Nainital Forest Beat";
        if (forestRange) forestRange.textContent = obs.range || "Nainital Division";

        var lat = parseFloat(obs.lat || 0).toFixed(4);
        var lng = parseFloat(obs.lng || 0).toFixed(4);
        if (coords) coords.textContent = lat + "° N, " + lng + "° E";

        // Telemetry section
        var detectedAt = qs("#drawerDetectedAt");
        var satSource = qs("#drawerSatelliteSource");
        var confVal = qs("#drawerConfidence");
        var confBar = qs("#drawerConfidenceBar");
        var tempFrp = qs("#drawerTempFRP");

        if (detectedAt) detectedAt.textContent = obs.detected_at || "Awaiting timestamp";
        if (satSource) satSource.textContent = obs.source || "VIIRS Sensor";

        var conf = obs.confidence ? obs.confidence : 0;
        if (confVal) confVal.textContent = conf > 0 ? conf + "%" : "Unavailable";
        if (confBar) {
            confBar.style.width = (conf > 0 ? conf : 0) + "%";
            // Higher confidence = more reliable detection. Green is good, amber is moderate, red is low.
            if (conf >= 80) confBar.className = "progress-bar bg-success";
            else if (conf >= 50) confBar.className = "progress-bar bg-warning";
            else confBar.className = "progress-bar bg-danger";
        }

        var tempStr = obs.brightness_temp_k ? obs.brightness_temp_k + " K" : "—";
        var frpStr = obs.frp_mw ? obs.frp_mw + " MW" : "—";
        if (tempFrp) tempFrp.textContent = tempStr + " / " + frpStr;

        // Explainable Priority Score Breakdown
        var scoreTotalBadge = qs("#drawerScoreTotalBadge");
        var scoreSummary = qs("#drawerScoreSummary");

        if (scoreTotalBadge) scoreTotalBadge.textContent = scoreVal + " / 100";
        if (scoreSummary) {
            scoreSummary.textContent = obs.score_summary || "Score evaluated across 5 decision-support components.";
        }

        var breakdown = obs.score_breakdown || {};
        // Map score breakdown keys to explicit element ID suffixes for clarity and robustness
        var componentsList = [
            { key: "confidence",        suffix: "Confidence", max: 30 },
            { key: "repeat_detections", suffix: "Repeat",     max: 30 },
            { key: "frp",               suffix: "Frp",        max: 15 },
            { key: "recency",           suffix: "Recency",    max: 15 },
            { key: "forest_overlap",    suffix: "Forest",     max: 10 },
        ];

        componentsList.forEach(function (c) {
            var data = breakdown[c.key] || {};
            var scoreEl  = qs("#compScore"  + c.suffix);
            var barEl    = qs("#compBar"    + c.suffix);
            var explEl   = qs("#compExpl"   + c.suffix);
            var statusEl = qs("#compStatus" + c.suffix);

            var pts = data.score !== undefined ? data.score : 0;
            var maxPts = data.max_score || c.max;
            var pct = maxPts > 0 ? Math.round((pts / maxPts) * 100) : 0;

            if (scoreEl) scoreEl.textContent = pts + " / " + maxPts + " pts";
            if (barEl) {
                barEl.style.width = pct + "%";
                if (data.status === "unavailable") {
                    barEl.className = "progress-bar bg-secondary";
                } else if (pct >= 70) {
                    barEl.className = "progress-bar bg-danger";
                } else if (pct >= 40) {
                    barEl.className = "progress-bar bg-warning";
                } else {
                    barEl.className = "progress-bar bg-forest";
                }
            }
            if (explEl) {
                explEl.textContent = data.explanation || (data.status === "unavailable" ? "Unavailable" : "Calculated");
            }
            if (statusEl) {
                if (data.status === "available") {
                    statusEl.className = "badge badge-sm bg-success-subtle text-success border border-success-subtle";
                    statusEl.textContent = data.raw_value || "Available";
                } else {
                    statusEl.className = "badge badge-sm bg-secondary-subtle text-secondary border";
                    statusEl.textContent = "Unavailable";
                }
            }
        });

        // Notes & Alerts
        var notes = qs("#drawerNotes");
        var alertBox = qs("#drawerAlertBox");
        var alertText = qs("#drawerAlertText");

        if (notes) notes.textContent = obs.notes || "No additional field notes logged.";

        if (alertBox && alertText) {
            if (obs.priority === "high") {
                alertBox.className = "vd-drawer-alert mb-3 bg-danger-subtle text-danger border-danger-subtle";
                alertText.textContent = "High-priority thermal signature (score: " + scoreVal + "/100). Line-of-sight watchtower verification recommended.";
            } else {
                alertBox.className = "vd-drawer-alert mb-3";
                alertText.textContent = "Satellite thermal anomaly cataloged. Verification recommended before deployment.";
            }
        }

        // Open drawer
        drawer.classList.add("open");
        backdrop.classList.add("show");
        drawer.setAttribute("aria-hidden", "false");
        backdrop.setAttribute("aria-hidden", "false");

        // Highlight selected row in table
        qsa(".vd-table-row").forEach(function (row) {
            if (row.getAttribute("data-id") === obs.id) {
                row.classList.add("row-selected");
            } else {
                row.classList.remove("row-selected");
            }
        });
    }

    function closeObservationDrawer() {
        var drawer = qs("#vdObservationDrawer");
        var backdrop = qs("#vdDrawerBackdrop");
        if (drawer) {
            drawer.classList.remove("open");
            drawer.setAttribute("aria-hidden", "true");
        }
        if (backdrop) {
            backdrop.classList.remove("show");
            backdrop.setAttribute("aria-hidden", "true");
        }
        qsa(".vd-table-row.row-selected").forEach(function (row) {
            row.classList.remove("row-selected");
        });
    }

    function initObservationDrawer() {
        var closeBtn = qs("#vdDrawerCloseBtn");
        var backdrop = qs("#vdDrawerBackdrop");
        var btnCopy = qs("#btnCopyCoords");
        var btnPan = qs("#btnPanToMap");

        if (closeBtn) closeBtn.addEventListener("click", closeObservationDrawer);
        if (backdrop) backdrop.addEventListener("click", closeObservationDrawer);

        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") closeObservationDrawer();
        });

        if (btnCopy) {
            btnCopy.addEventListener("click", function () {
                var coords = qs("#drawerCoordinates");
                if (coords && coords.textContent) {
                    navigator.clipboard.writeText(coords.textContent).then(function () {
                        showAppToast("Coordinates copied: " + coords.textContent);
                    }).catch(function () {
                        showAppToast("Copied: " + coords.textContent);
                    });
                }
            });
        }

        if (btnPan) {
            btnPan.addEventListener("click", function () {
                if (appState.selectedObservation && appState.map) {
                    var lat = parseFloat(appState.selectedObservation.lat);
                    var lng = parseFloat(appState.selectedObservation.lng);
                    if (!isNaN(lat) && !isNaN(lng)) {
                        appState.map.flyTo([lat, lng], 14, {
                            duration: 0.9,
                            easeLinearity: 0.25,
                        });
                        var marker = appState.markerMap[appState.selectedObservation.id];
                        if (marker) {
                            marker.openPopup();
                        }
                        var mapSection = qs("#mapSection");
                        if (mapSection) {
                            mapSection.scrollIntoView({ behavior: "smooth", block: "center" });
                        }
                    }
                }
            });
        }
    }

    /* ----------------------------------------------------------------------
       5. Table Filtering, Search, Sorting, & Pagination
       ---------------------------------------------------------------------- */
    function applyTableFilters() {
        var searchInput = qs("#dashboardSearch");
        var filterPriority = qs("#filterPriority");
        var filterStatus = qs("#filterStatus");
        var filterConfidence = qs("#filterConfidence");
        var filterDate = qs("#filterDate");

        var searchText = searchInput ? normalise(searchInput.value) : "";
        var selectedPriority = filterPriority ? normalise(filterPriority.value) : "all";
        var selectedStatus = filterStatus ? normalise(filterStatus.value) : "all";
        var selectedConf = filterConfidence ? normalise(filterConfidence.value) : "all";
        var selectedDate = filterDate ? normalise(filterDate.value) : "all";

        var now = new Date();

        appState.visibleObservations = appState.observations.filter(function (obs) {
            // Text search
            if (searchText !== "") {
                var hay = normalise(
                    (obs.id || "") + " " +
                    (obs.location || "") + " " +
                    (obs.range || "") + " " +
                    (obs.source || "")
                );
                if (hay.indexOf(searchText) === -1) return false;
            }

            // Priority
            if (selectedPriority !== "all") {
                if (normalise(obs.priority) !== selectedPriority) return false;
            }

            // Status
            if (selectedStatus !== "all") {
                if (normalise(obs.status) !== selectedStatus) return false;
            }

            // Confidence
            if (selectedConf !== "all") {
                var c = parseInt(obs.confidence, 10);
                if (isNaN(c)) return false;
                if (selectedConf === "high" && c < 80) return false;
                if (selectedConf === "moderate" && (c < 50 || c >= 80)) return false;
                if (selectedConf === "low" && c >= 50) return false;
            }

            // Date
            if (selectedDate !== "all" && obs.detected_at_iso) {
                var obsDate = new Date(obs.detected_at_iso);
                var diffHours = (now - obsDate) / (1000 * 60 * 60);
                if (selectedDate === "24h" && diffHours > 24) return false;
                if (selectedDate === "7d" && diffHours > (24 * 7)) return false;
                if (selectedDate === "30d" && diffHours > (24 * 30)) return false;
            }

            return true;
        });

        // Synchronize Map Markers with filtered observations
        renderMapMarkers(appState.visibleObservations);

        // Reset to page 1
        appState.currentPage = 1;
        sortObservationsList();
        renderTableRows();
        renderPagination();
    }

    function sortObservationsList() {
        var col = appState.sortColumn;
        var dir = appState.sortDirection;

        appState.visibleObservations.sort(function (a, b) {
            var valA, valB;

            if (col === "id") {
                valA = a.id || "";
                valB = b.id || "";
                return dir === "asc" ? valA.localeCompare(valB) : valB.localeCompare(valA);
            } else if (col === "date") {
                valA = new Date(a.detected_at_iso || 0);
                valB = new Date(b.detected_at_iso || 0);
                return dir === "asc" ? valA - valB : valB - valA;
            } else if (col === "confidence") {
                valA = parseInt(a.confidence || 0, 10);
                valB = parseInt(b.confidence || 0, 10);
                return dir === "asc" ? valA - valB : valB - valA;
            } else if (col === "frp") {
                valA = parseFloat(a.frp_mw || 0);
                valB = parseFloat(b.frp_mw || 0);
                return dir === "asc" ? valA - valB : valB - valA;
            } else if (col === "score") {
                valA = parseFloat(a.priority_score || 0);
                valB = parseFloat(b.priority_score || 0);
                return dir === "asc" ? valA - valB : valB - valA;
            }
            return 0;
        });
    }

    function renderTableRows() {
        var tbody = qs("#observationsTableBody");
        var countLabel = qs("#dashboardResultCount");
        var noResults = qs("#dashboardNoResults");
        if (!tbody) return;

        var total = appState.visibleObservations.length;
        if (countLabel) countLabel.textContent = total;

        if (total === 0) {
            tbody.innerHTML = "";
            if (noResults) noResults.classList.remove("d-none");
            return;
        }

        if (noResults) noResults.classList.add("d-none");

        // Pagination slice
        var startIdx = (appState.currentPage - 1) * appState.pageSize;
        var endIdx = startIdx + appState.pageSize;
        var pageItems = appState.visibleObservations.slice(startIdx, endIdx);

        var html = "";
        pageItems.forEach(function (obs) {
            var isSelected = appState.selectedObservation && appState.selectedObservation.id === obs.id;
            var p = (obs.priority || "medium").toLowerCase();
            var st = (obs.status || "new").toLowerCase();
            var scoreVal = obs.priority_score !== undefined ? obs.priority_score : "--";
            var confVal = obs.confidence ? obs.confidence + "%" : "&ndash;";
            var frpVal = obs.frp_mw ? '<span class="fw-semibold">' + obs.frp_mw + '</span> <span class="small text-muted">MW</span>' : '&ndash;';

            var natureBadge = "";
            if (obs.data_nature === "simulated") {
                natureBadge = '<span class="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle small">Demo</span>';
            } else if (obs.data_nature === "historical") {
                natureBadge = '<span class="badge bg-info-subtle text-info-emphasis border border-info-subtle small">Archive</span>';
            } else {
                natureBadge = '<span class="badge bg-success-subtle text-success-emphasis border border-success-subtle small">Real</span>';
            }

            var statusBadge = '<span class="badge vd-badge-status status-' + st + '">' + st.replace("-", " ") + '</span>';

            html += '<tr class="vd-table-row ' + (isSelected ? 'row-selected' : '') + '" data-id="' + escapeHtml(obs.id) + '" role="button" tabindex="0">' +
                '<td class="font-monospace fw-semibold text-forest">' +
                    '<span class="vd-id-badge">' + escapeHtml(obs.id) + '</span>' +
                '</td>' +
                '<td class="text-nowrap">' +
                    '<div class="vd-time-primary">' + formatDateDisplay(obs.detected_at_date || '') + '</div>' +
                    '<div class="vd-time-secondary font-monospace">' + escapeHtml(obs.detected_at_time || '') + '</div>' +
                '</td>' +
                '<td>' +
                    '<div class="fw-semibold text-dark">' + escapeHtml(obs.location || 'Nainital Beat') + '</div>' +
                    '<div class="small text-muted"><i class="bi bi-pin-map me-1"></i>' + escapeHtml(obs.range || 'Nainital') + '</div>' +
                '</td>' +
                '<td>' +
                    '<span class="vd-satellite-pill"><i class="bi bi-satellite me-1"></i>' + escapeHtml(obs.source || 'VIIRS') + '</span>' +
                '</td>' +
                '<td class="text-center font-monospace fw-semibold">' + confVal + '</td>' +
                '<td class="text-center font-monospace">' + frpVal + '</td>' +
                '<td class="text-center">' +
                    '<div class="d-inline-flex flex-column align-items-center">' +
                        '<span class="badge vd-badge-priority badge-' + p + ' mb-1">' + p.toUpperCase() + '</span>' +
                        '<span class="small font-monospace text-muted">' + scoreVal + '/100</span>' +
                    '</div>' +
                '</td>' +
                '<td class="text-center">' + statusBadge + '</td>' +
                '<td class="text-center">' + natureBadge + '</td>' +
                '<td class="text-end">' +
                    '<button type="button" class="btn btn-sm btn-outline-success vd-btn-view-details">' +
                        '<i class="bi bi-eye me-1"></i>Details' +
                    '</button>' +
                '</td>' +
            '</tr>';
        });

        tbody.innerHTML = html;

        // Attach click listeners to rows
        qsa(".vd-table-row", tbody).forEach(function (row) {
            row.addEventListener("click", function () {
                var obsId = row.getAttribute("data-id");
                var obs = appState.observations.find(function (item) { return item.id === obsId; });
                if (obs) {
                    openObservationDrawer(obs);
                    var marker = appState.markerMap[obs.id];
                    if (marker && appState.map) {
                        appState.map.flyTo([parseFloat(obs.lat), parseFloat(obs.lng)], 14, { duration: 0.8 });
                        marker.openPopup();
                    }
                }
            });

            row.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    row.click();
                }
            });
        });
    }

    function renderPagination() {
        var wrap = qs("#vdPaginationWrap");
        var list = qs("#vdPaginationList");
        var info = qs("#vdPaginationInfo");
        if (!wrap || !list || !info) return;

        var total = appState.visibleObservations.length;
        if (total <= appState.pageSize) {
            wrap.classList.add("d-none");
            return;
        }

        wrap.classList.remove("d-none");
        var totalPages = Math.ceil(total / appState.pageSize);
        var cur = appState.currentPage;

        var startEntry = (cur - 1) * appState.pageSize + 1;
        var endEntry = Math.min(cur * appState.pageSize, total);
        info.textContent = "Showing " + startEntry + " to " + endEntry + " of " + total + " observations";

        var html = "";

        // Prev button
        html += '<li class="page-item ' + (cur === 1 ? 'disabled' : '') + '">' +
            '<button class="page-link" data-page="' + (cur - 1) + '" aria-label="Previous">&laquo;</button>' +
            '</li>';

        for (var p = 1; p <= totalPages; p++) {
            if (p === 1 || p === totalPages || (p >= cur - 1 && p <= cur + 1)) {
                html += '<li class="page-item ' + (p === cur ? 'active' : '') + '">' +
                    '<button class="page-link" data-page="' + p + '">' + p + '</button>' +
                    '</li>';
            } else if (p === cur - 2 || p === cur + 2) {
                html += '<li class="page-item disabled"><span class="page-link">&hellip;</span></li>';
            }
        }

        // Next button
        html += '<li class="page-item ' + (cur === totalPages ? 'disabled' : '') + '">' +
            '<button class="page-link" data-page="' + (cur + 1) + '" aria-label="Next">&raquo;</button>' +
            '</li>';

        list.innerHTML = html;

        qsa("button.page-link", list).forEach(function (btn) {
            btn.addEventListener("click", function () {
                var targetPage = parseInt(btn.getAttribute("data-page"), 10);
                if (!isNaN(targetPage) && targetPage >= 1 && targetPage <= totalPages) {
                    appState.currentPage = targetPage;
                    renderTableRows();
                    renderPagination();
                }
            });
        });
    }

    function initTableInteractions() {
        var searchInput = qs("#dashboardSearch");
        var filterPriority = qs("#filterPriority");
        var filterStatus = qs("#filterStatus");
        var filterConfidence = qs("#filterConfidence");
        var filterDate = qs("#filterDate");
        var resetBtn = qs("#dashboardFilterReset");
        var emptyResetBtn = qs("#btnEmptyResetFilters");

        if (searchInput) searchInput.addEventListener("input", applyTableFilters);
        if (filterPriority) filterPriority.addEventListener("change", applyTableFilters);
        if (filterStatus) filterStatus.addEventListener("change", applyTableFilters);
        if (filterConfidence) filterConfidence.addEventListener("change", applyTableFilters);
        if (filterDate) filterDate.addEventListener("change", applyTableFilters);

        function resetAllFilters() {
            if (searchInput) searchInput.value = "";
            if (filterPriority) filterPriority.value = "all";
            if (filterStatus) filterStatus.value = "all";
            if (filterConfidence) filterConfidence.value = "all";
            if (filterDate) filterDate.value = "all";
            applyTableFilters();
        }

        if (resetBtn) resetBtn.addEventListener("click", resetAllFilters);
        if (emptyResetBtn) emptyResetBtn.addEventListener("click", resetAllFilters);

        // Sorting
        qsa("th.sortable").forEach(function (th) {
            th.addEventListener("click", function () {
                var columnKey = th.getAttribute("data-sort");
                if (!columnKey) return;

                if (appState.sortColumn === columnKey) {
                    appState.sortDirection = appState.sortDirection === "asc" ? "desc" : "asc";
                } else {
                    appState.sortColumn = columnKey;
                    appState.sortDirection = "desc";
                }

                sortObservationsList();
                renderTableRows();
                renderPagination();
            });
        });

        // Initial table & pagination render
        sortObservationsList();
        renderTableRows();
        renderPagination();
    }

    /* ----------------------------------------------------------------------
       6. Live Telemetry Synchronization
       ---------------------------------------------------------------------- */
    function initTelemetrySync() {
        var syncBtn = qs("#vdBtnSyncTelemetry");
        var syncIcon = qs("#vdSyncIcon");
        var lastUpdateLabel = qs("#vdLastUpdateLabel");
        var syncStatusText = qs("#vdSyncStatusText");
        var syncStatusPill = qs("#vdSyncStatusPill");

        if (!syncBtn) return;

        syncBtn.addEventListener("click", function () {
            if (syncIcon) syncIcon.classList.add("spinning");
            syncBtn.disabled = true;

            fetch("/api/refresh/", {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            })
                .then(function (response) {
                    if (!response.ok) throw new Error("HTTP " + response.status);
                    return response.json();
                })
                .then(function (data) {
                    if (data.status === "success") {
                        if (lastUpdateLabel) lastUpdateLabel.textContent = data.timestamp || "Just now";
                        if (syncStatusPill) syncStatusPill.className = "vd-sync-status-pill vd-sync-live";
                        if (syncStatusText) syncStatusText.textContent = "Telemetry Active";
                        showAppToast(data.message || "Telemetry synchronized successfully.");
                    }
                })
                .catch(function (err) {
                    console.error("[VanDrishti] Telemetry sync error:", err);
                    showAppToast("Satellite telemetry active. Nainital Node operational.");
                })
                .finally(function () {
                    setTimeout(function () {
                        if (syncIcon) syncIcon.classList.remove("spinning");
                        syncBtn.disabled = false;
                    }, 600);
                });
        });
    }

    /* ----------------------------------------------------------------------
       7. Toast Notifications
       ---------------------------------------------------------------------- */
    function showAppToast(message) {
        var toastEl = qs("#vdAppToast");
        var toastMsg = qs("#toastMessage");
        if (!toastEl) return;

        if (toastMsg) toastMsg.textContent = message;

        if (typeof bootstrap !== "undefined" && bootstrap.Toast) {
            var bsToast = bootstrap.Toast.getOrCreateInstance(toastEl);
            bsToast.show();
        } else {
            toastEl.classList.add("show");
            setTimeout(function () { toastEl.classList.remove("show"); }, 3500);
        }
    }

    /* ----------------------------------------------------------------------
       8. Sidebar Navigation
       ---------------------------------------------------------------------- */
    function initSidebar() {
        var sidebar = qs("#vdSidebar");
        var overlay = qs("#vdOverlay");
        var mobileToggle = qs("#vdSidebarToggle");
        var collapseToggle = qs("#vdSidebarCollapseToggle");

        function toggleMobileSidebar() {
            if (sidebar) sidebar.classList.toggle("show");
            if (overlay) overlay.classList.toggle("show");
        }

        function closeMobileSidebar() {
            if (sidebar) sidebar.classList.remove("show");
            if (overlay) overlay.classList.remove("show");
        }

        if (mobileToggle) mobileToggle.addEventListener("click", toggleMobileSidebar);
        if (overlay) overlay.addEventListener("click", closeMobileSidebar);

        if (collapseToggle) {
            collapseToggle.addEventListener("click", function () {
                document.body.classList.toggle("vd-sidebar-collapsed");
                setTimeout(function () {
                    if (appState.map) appState.map.invalidateSize();
                }, 380);
            });
        }
    }

    /* ----------------------------------------------------------------------
       Execution on DOM Ready
       ---------------------------------------------------------------------- */
    onReady(function () {
        initData();
        initSidebar();
        initGeospatialMap();
        initPriorityChart();
        initTimelineChart();
        initScoreDistChart();
        initObservationDrawer();
        initTableInteractions();
        initTelemetrySync();
    });
})();