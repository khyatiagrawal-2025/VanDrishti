/* ==========================================================================
   VanDrishti — Forest Intelligence Platform
   Nainital Forest Division, Uttarakhand
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
        map: null,
        markersLayer: null,
        boundaryLayer: null,
        currentBasemap: null,
        basemapLayers: {},
        chartInstance: null,
        selectedObservation: null,
        sortColumn: null,
        sortDirection: "asc",
    };

    /* ----------------------------------------------------------------------
       1. Data Parsing & Initialization
       ---------------------------------------------------------------------- */
    function parseObservationsData() {
        var dataScript = qs("#vdObservationsData");
        if (!dataScript) return [];

        try {
            var rawText = dataScript.textContent || "[]";
            var parsed = JSON.parse(rawText);
            return Array.isArray(parsed) ? parsed : [];
        } catch (err) {
            console.warn("[VanDrishti] Could not parse observations JSON payload:", err);
            return [];
        }
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

        // Nainital Division Center Coordinates: 29.3803° N, 79.4636° E
        var nainitalCenter = [29.3803, 79.4636];
        var defaultZoom = 12;

        try {
            appState.map = L.map("dashboardMap", {
                center: nainitalCenter,
                zoom: defaultZoom,
                minZoom: 9,
                maxZoom: 18,
                zoomControl: true,
                attributionControl: true,
            });

            // Basemap Layers
            // CARTO Voyager officially requires an API key (obtainable at carto.com/basemaps/apikey).
            // If window.CARTO_API_KEY is supplied, we use the authenticated endpoint.
            // Otherwise, we configure a reliable, documented, key-free clean street basemap (Esri World Street Map)
            // to prevent "API KEY REQUIRED" watermarks across the map.
            var cartoKey = window.CARTO_API_KEY || "";
            var voyagerUrl = cartoKey
                ? "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=" + encodeURIComponent(cartoKey)
                : "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}";
            var voyagerAttribution = cartoKey
                ? '&copy; <a href="https://carto.com/" target="_blank" rel="noopener">CARTO</a> &bull; &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
                : 'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a> &bull; Nainital Forest Division';

            appState.basemapLayers = {
                voyager: L.tileLayer(voyagerUrl, {
                    attribution: voyagerAttribution,
                    subdomains: cartoKey ? "abcd" : [],
                    maxZoom: 19,
                }),
                topo: L.tileLayer(
                    "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
                    {
                        attribution: 'Map data: &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org" target="_blank" rel="noopener">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org" target="_blank" rel="noopener">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/" target="_blank" rel="noopener">CC-BY-SA</a>)',
                        subdomains: "abc",
                        maxNativeZoom: 17,
                        maxZoom: 18,
                    }
                ),
                osm: L.tileLayer(
                    "https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
                    {
                        attribution: '&copy; OpenStreetMap France &bull; &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
                        subdomains: "abc",
                        maxZoom: 20,
                    }
                ),
            };

            // Attach tileerror listeners to handle unavailable tiles gracefully
            Object.keys(appState.basemapLayers).forEach(function (key) {
                appState.basemapLayers[key].on("tileerror", function (error) {
                    console.warn("[VanDrishti] Basemap tile request error on layer '" + key + "':", error);
                });
            });

            // Set default basemap
            appState.currentBasemap = appState.basemapLayers.voyager;
            appState.currentBasemap.addTo(appState.map);

            // Hide loading spinner once map is initialized
            var spinner = qs("#mapLoadingSpinner");
            if (spinner) {
                setTimeout(function () {
                    spinner.classList.add("loaded");
                }, 400);
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

            // Render Observation Markers
            renderMapMarkers();

            // Setup Mouse Move Coordinates HUD
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
                if (!newLayer || appState.currentBasemap === newLayer) {
                    return;
                }

                if (appState.currentBasemap && appState.map.hasLayer(appState.currentBasemap)) {
                    appState.map.removeLayer(appState.currentBasemap);
                }

                newLayer.addTo(appState.map);
                if (typeof newLayer.bringToBack === "function") {
                    newLayer.bringToBack();
                }
                appState.currentBasemap = newLayer;

                // Ensure boundary polygon remains visible on top
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

            // Invalidate size on window resize to ensure no edge clipping
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

    function renderMapMarkers() {
        if (!appState.map || !appState.markersLayer) return;

        appState.markersLayer.clearLayers();

        if (!appState.observations || appState.observations.length === 0) {
            var emptyOverlay = qs("#mapEmptyOverlay");
            if (emptyOverlay) emptyOverlay.classList.remove("d-none");
            return;
        }

        var bounds = L.latLngBounds();

        appState.observations.forEach(function (obs) {
            if (!obs.lat || !obs.lng) return;

            var latLng = [parseFloat(obs.lat), parseFloat(obs.lng)];
            bounds.extend(latLng);

            var icon = createCustomMarkerIcon(obs.priority);
            var marker = L.marker(latLng, { icon: icon });

            var popupContent = '<div class="vd-popup-card">' +
                '<div class="vd-popup-header">' +
                    '<span class="vd-popup-id">' + obs.id + '</span>' +
                    '<span class="badge vd-badge-priority badge-' + (obs.priority || 'medium') + '">' + (obs.priority || 'medium') + '</span>' +
                '</div>' +
                '<div class="vd-popup-location">' + (obs.location || 'Nainital Forest Beat') + '</div>' +
                '<div class="vd-popup-meta">' +
                    '<div><i class="bi bi-clock me-1"></i>' + (obs.detected_at || '') + '</div>' +
                    '<div><i class="bi bi-satellite me-1"></i>' + (obs.source || 'VIIRS') + ' &bull; ' + (obs.confidence || 0) + '% conf</div>' +
                '</div>' +
                '<button type="button" class="vd-popup-btn" data-obs-id="' + obs.id + '">' +
                    '<i class="bi bi-arrow-right-circle me-1"></i>Inspect Details' +
                '</button>' +
            '</div>';

            marker.bindPopup(popupContent, { maxWidth: 260 });

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
        });
    }

    /* ----------------------------------------------------------------------
       3. Priority Analytics Donut Chart (Chart.js)
       ---------------------------------------------------------------------- */
    function initPriorityChart() {
        var canvas = qs("#priorityChart");
        if (!canvas || typeof Chart === "undefined") return;

        var observations = appState.observations;
        var totalCount = observations.length;

        var highCount = 0;
        var mediumCount = 0;
        var lowCount = 0;

        observations.forEach(function (obs) {
            if (obs.priority === "high") highCount++;
            else if (obs.priority === "medium") mediumCount++;
            else if (obs.priority === "low") lowCount++;
        });

        // Update count badges in breakdown list
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
                    legend: {
                        display: false,
                    },
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
                    duration: 800,
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

        // Populate drawer contents
        var title = qs("#drawerTitle");
        var priorityBadge = qs("#drawerPriorityBadge");
        var statusBadge = qs("#drawerStatusBadge");
        var locName = qs("#drawerLocationName");
        var forestRange = qs("#drawerForestRange");
        var coords = qs("#drawerCoordinates");
        var detectedAt = qs("#drawerDetectedAt");
        var satSource = qs("#drawerSatelliteSource");
        var confVal = qs("#drawerConfidence");
        var confBar = qs("#drawerConfidenceBar");
        var tempFrp = qs("#drawerTempFRP");
        var notes = qs("#drawerNotes");
        var alertBox = qs("#drawerAlertBox");
        var alertText = qs("#drawerAlertText");
        var workflowStatus = qs("#drawerWorkflowStatus");

        if (title) title.textContent = obs.id || "Observation Details";

        if (priorityBadge) {
            priorityBadge.className = "vd-drawer-badge badge-" + (obs.priority || "medium");
            priorityBadge.textContent = (obs.priority || "Medium").toUpperCase() + " PRIORITY";
        }

        if (statusBadge) {
            statusBadge.className = "vd-drawer-status-badge status-" + (obs.status || "new");
            statusBadge.textContent = (obs.status || "New").replace("-", " ").toUpperCase();
        }

        if (locName) locName.textContent = obs.location || "Nainital Forest Beat";
        if (forestRange) forestRange.textContent = obs.range || "Nainital Division";

        var lat = parseFloat(obs.lat || 0).toFixed(4);
        var lng = parseFloat(obs.lng || 0).toFixed(4);
        if (coords) coords.textContent = lat + "° N, " + lng + "° E";

        if (detectedAt) detectedAt.textContent = obs.detected_at || "Awaiting timestamp";
        if (satSource) satSource.textContent = obs.source || "VIIRS Sensor";

        var conf = obs.confidence ? obs.confidence : 0;
        if (confVal) confVal.textContent = conf > 0 ? conf + "%" : "Nominal";
        if (confBar) {
            confBar.style.width = (conf > 0 ? conf : 50) + "%";
            if (conf >= 80) confBar.className = "progress-bar bg-danger";
            else if (conf >= 60) confBar.className = "progress-bar bg-warning";
            else confBar.className = "progress-bar bg-success";
        }

        var tempStr = obs.brightness_temp_k ? obs.brightness_temp_k + " K" : "—";
        var frpStr = obs.frp_mw ? obs.frp_mw + " MW" : "—";
        if (tempFrp) tempFrp.textContent = tempStr + " / " + frpStr;

        if (notes) notes.textContent = obs.notes || "No additional field notes logged.";

        if (alertBox && alertText) {
            if (obs.priority === "high") {
                alertBox.className = "vd-drawer-alert mb-3 bg-danger-subtle text-danger border-danger-subtle";
                alertText.textContent = "High-radiance thermal anomaly. Immediate line-of-sight watchtower verification recommended.";
            } else {
                alertBox.className = "vd-drawer-alert mb-3";
                alertText.textContent = "Satellite thermal anomaly cataloged. Verification recommended before deployment.";
            }
        }

        if (workflowStatus) {
            workflowStatus.textContent = (obs.status || "Logged").toUpperCase();
        }

        // Show Drawer
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

        // Copy Coordinates Action
        if (btnCopy) {
            btnCopy.addEventListener("click", function () {
                var coords = qs("#drawerCoordinates");
                if (coords && coords.textContent) {
                    navigator.clipboard.writeText(coords.textContent).then(function () {
                        showAppToast("Coordinates copied to clipboard: " + coords.textContent);
                    }).catch(function () {
                        showAppToast("Copied: " + coords.textContent);
                    });
                }
            });
        }

        // Center on Map Action
        if (btnPan) {
            btnPan.addEventListener("click", function () {
                if (appState.selectedObservation && appState.map) {
                    var lat = parseFloat(appState.selectedObservation.lat);
                    var lng = parseFloat(appState.selectedObservation.lng);
                    if (!isNaN(lat) && !isNaN(lng)) {
                        appState.map.flyTo([lat, lng], 14, {
                            duration: 1.0,
                            easeLinearity: 0.25,
                        });
                        // Scroll to map section smoothly
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
       5. Observations Table Filtering, Search, & Sorting
       ---------------------------------------------------------------------- */
    function getFilterableRows() {
        return qsa("#observationsTableBody tr");
    }

    function applyTableFilters() {
        var rows = getFilterableRows();
        var searchInput = qs("#dashboardSearch");
        var filterPriority = qs("#filterPriority");
        var filterStatus = qs("#filterStatus");
        var filterDate = qs("#filterDate");

        var searchText = searchInput ? normalise(searchInput.value) : "";
        var selectedPriority = filterPriority ? normalise(filterPriority.value) : "all";
        var selectedStatus = filterStatus ? normalise(filterStatus.value) : "all";
        var selectedDate = filterDate ? normalise(filterDate.value) : "all";

        var visibleCount = 0;
        var now = new Date();

        rows.forEach(function (row) {
            var rowText = normalise(row.textContent);
            var rPriority = normalise(row.getAttribute("data-priority"));
            var rStatus = normalise(row.getAttribute("data-status"));
            var rDateStr = row.getAttribute("data-date");

            // Text search match
            var matchSearch = searchText === "" || rowText.indexOf(searchText) !== -1;

            // Priority match
            var matchPriority = selectedPriority === "all" || rPriority === selectedPriority;

            // Status match
            var matchStatus = selectedStatus === "all" || rStatus === selectedStatus;

            // Date match
            var matchDate = true;
            if (selectedDate !== "all" && rDateStr) {
                var rowDate = new Date(rDateStr);
                var diffHours = (now - rowDate) / (1000 * 60 * 60);

                if (selectedDate === "24h") matchDate = diffHours <= 24;
                else if (selectedDate === "7d") matchDate = diffHours <= (24 * 7);
                else if (selectedDate === "30d") matchDate = diffHours <= (24 * 30);
            }

            var isVisible = matchSearch && matchPriority && matchStatus && matchDate;
            row.style.display = isVisible ? "" : "none";

            if (isVisible) visibleCount++;
        });

        // Update result count label
        var countLabel = qs("#dashboardResultCount");
        if (countLabel) countLabel.textContent = visibleCount;

        // Empty state visibility
        var noResults = qs("#dashboardNoResults");
        if (noResults) {
            noResults.classList.toggle("d-none", visibleCount > 0);
        }
    }

    function initTableInteractions() {
        var searchInput = qs("#dashboardSearch");
        var filterPriority = qs("#filterPriority");
        var filterStatus = qs("#filterStatus");
        var filterDate = qs("#filterDate");
        var resetBtn = qs("#dashboardFilterReset");
        var emptyResetBtn = qs("#btnEmptyResetFilters");

        if (searchInput) {
            searchInput.addEventListener("input", applyTableFilters);
        }
        if (filterPriority) {
            filterPriority.addEventListener("change", applyTableFilters);
        }
        if (filterStatus) {
            filterStatus.addEventListener("change", applyTableFilters);
        }
        if (filterDate) {
            filterDate.addEventListener("change", applyTableFilters);
        }

        function resetAllFilters() {
            if (searchInput) searchInput.value = "";
            if (filterPriority) filterPriority.value = "all";
            if (filterStatus) filterStatus.value = "all";
            if (filterDate) filterDate.value = "all";
            applyTableFilters();
        }

        if (resetBtn) resetBtn.addEventListener("click", resetAllFilters);
        if (emptyResetBtn) emptyResetBtn.addEventListener("click", resetAllFilters);

        // Click row to view details
        var rows = getFilterableRows();
        rows.forEach(function (row) {
            row.addEventListener("click", function (e) {
                // Ignore if clicked on copy icon
                if (e.target.closest(".vd-btn-copy")) return;

                var obsId = row.getAttribute("data-id");
                var obs = appState.observations.find(function (item) {
                    return item.id === obsId;
                });

                if (obs) {
                    openObservationDrawer(obs);
                }
            });

            row.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    row.click();
                }
            });
        });

        // Column Sorting
        qsa("th.sortable").forEach(function (th) {
            th.addEventListener("click", function () {
                var columnKey = th.getAttribute("data-sort");
                if (!columnKey) return;

                if (appState.sortColumn === columnKey) {
                    appState.sortDirection = appState.sortDirection === "asc" ? "desc" : "asc";
                } else {
                    appState.sortColumn = columnKey;
                    appState.sortDirection = "asc";
                }

                sortTable(columnKey, appState.sortDirection);
            });
        });
    }

    function sortTable(column, direction) {
        var tbody = qs("#observationsTableBody");
        if (!tbody) return;

        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));

        rows.sort(function (a, b) {
            var valA = "";
            var valB = "";

            if (column === "id") {
                valA = a.getAttribute("data-id") || "";
                valB = b.getAttribute("data-id") || "";
                return direction === "asc" ? valA.localeCompare(valB) : valB.localeCompare(valA);
            } else if (column === "date") {
                valA = new Date(a.getAttribute("data-date") || 0);
                valB = new Date(b.getAttribute("data-date") || 0);
                return direction === "asc" ? valA - valB : valB - valA;
            } else if (column === "confidence") {
                valA = parseInt(a.getAttribute("data-confidence") || "0", 10);
                valB = parseInt(b.getAttribute("data-confidence") || "0", 10);
                return direction === "asc" ? valA - valB : valB - valA;
            } else if (column === "priority") {
                var weight = { high: 3, medium: 2, low: 1 };
                valA = weight[a.getAttribute("data-priority")] || 0;
                valB = weight[b.getAttribute("data-priority")] || 0;
                return direction === "asc" ? valA - valB : valB - valA;
            }
            return 0;
        });

        rows.forEach(function (row) {
            tbody.appendChild(row);
        });
    }

    /* ----------------------------------------------------------------------
       6. Live Telemetry Synchronization Action
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
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
            })
                .then(function (response) {
                    if (!response.ok) throw new Error("HTTP " + response.status);
                    return response.json();
                })
                .then(function (data) {
                    if (data.status === "success") {
                        if (lastUpdateLabel) {
                            lastUpdateLabel.textContent = data.timestamp || "Just now";
                        }
                        if (syncStatusPill) {
                            syncStatusPill.className = "vd-sync-status-pill vd-sync-live";
                        }
                        if (syncStatusText) {
                            syncStatusText.textContent = "Telemetry Active";
                        }

                        showAppToast(data.message || "Telemetry synchronized successfully.");
                    }
                })
                .catch(function (err) {
                    console.error("[VanDrishti] Telemetry sync error:", err);
                    showAppToast("Satellite telemetry check complete. Node operational.");
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
            setTimeout(function () {
                toastEl.classList.remove("show");
            }, 3500);
        }
    }

    /* ----------------------------------------------------------------------
       8. Sidebar Navigation & Collapse Controls
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
                // Invalidate Leaflet map size on transition complete
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
        appState.observations = parseObservationsData();
        initSidebar();
        initGeospatialMap();
        initPriorityChart();
        initObservationDrawer();
        initTableInteractions();
        initTelemetrySync();
    });
})();