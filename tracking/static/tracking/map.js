(function () {
  "use strict";

  const mapElement = document.getElementById("map");
  const initialElement = document.getElementById("initial-snapshot");
  if (!mapElement || !initialElement) return;

  const mode = mapElement.dataset.mode;
  const snapshotUrl = mapElement.dataset.snapshotUrl;
  const renameTemplate = mapElement.dataset.renameTemplate || "";
  const lapTemplate = mapElement.dataset.lapTemplate || "";
  const connection = document.getElementById("connection");
  const connectionText = document.getElementById("connection-text");
  const lastRefresh = document.getElementById("last-refresh");
  const listElement = document.getElementById("runner-list");
  const emptyElement = document.getElementById("runner-empty");
  const searchElement = document.getElementById("runner-search");
  const fitButton = document.getElementById("fit-route");
  const renameForm = document.getElementById("rename-form");
  const renameInput = document.getElementById("rename-input");
  const lapForm = document.getElementById("lap-form");
  const lapCount = document.getElementById("lap-count");
  const summary = {
    participants: document.getElementById("summary-participants"),
    live: document.getElementById("summary-live"),
    stale: document.getElementById("summary-stale"),
    liveTile: document.getElementById("summary-live-tile"),
    staleTile: document.getElementById("summary-stale-tile"),
  };
  const liveChip = document.getElementById("map-live-chip");
  const liveChipCount = document.getElementById("map-live-count");
  const liveChipLabel = document.getElementById("map-live-label");
  const runnerBadge = document.getElementById("runner-badge");
  const mapWarning = document.getElementById("map-warning");
  const routeStats = document.getElementById("route-stats");
  const routeDistance = document.getElementById("route-distance");
  const routeClimbTile = document.getElementById("route-climb-tile");
  const routeClimb = document.getElementById("route-climb");

  // ui.js is deferred, so resolve it lazily rather than at parse time.
  function ui() {
    return window.RunlineUI || {};
  }
  const STATUS_LABELS = {
    live: "Live",
    stale: "Stale",
    waiting: "Waiting",
    expired: "Expired",
    stopped: "Stopped",
    registered: "Registered",
  };
  const STALE_MS = 120000;
  const POLL_MS = 5000;

  let snapshot = JSON.parse(initialElement.textContent);
  let map = null;
  let routeLayer = null;
  let routeSignature = "";
  let mapFitted = false;
  let tileLayer = null;
  let markers = new Map();
  let rows = new Map();
  let selectedId = null;
  let stopped = false;
  let timer = null;
  let lastServerTime = Date.parse(snapshot.server_time) || Date.now();
  let lastFetchAt = Date.now();
  let lastSuccessfulAt = new Date();
  let connectionState = "loading";

  function clock(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function setConnection(text, state) {
    connectionState = state;
    connectionText.textContent = text;
    connection.dataset.state = state;
  }

  function serverNow() {
    return lastServerTime + (Date.now() - lastFetchAt);
  }

  function effectiveStatus(runner, now) {
    if (runner.status !== "live" || !runner.position_at) return runner.status;
    const position = Date.parse(runner.position_at);
    if (Number.isNaN(position)) return "stale";
    return position >= now - STALE_MS ? "live" : "stale";
  }

  function statusElement(runner, status) {
    const element = document.createElement("span");
    element.className = "runner-row__status status-text status-text--" + status;
    element.textContent = STATUS_LABELS[status] || status;
    return element;
  }

  function popupContent(runner, status) {
    const wrapper = document.createElement("div");
    wrapper.className = "map-popup";
    const name = document.createElement("strong");
    name.textContent = runner.name;
    wrapper.appendChild(name);
    const parts = [STATUS_LABELS[status] || status];
    if (typeof runner.laps === "number") {
      parts.push(runner.laps + (runner.laps === 1 ? " lap" : " laps"));
    }
    if (runner.position_at) parts.push("last fix " + clock(runner.position_at));
    if (runner.accuracy !== null && runner.accuracy !== undefined) {
      parts.push("±" + Math.round(runner.accuracy) + " m");
    }
    parts.forEach(function (part) {
      const meta = document.createElement("span");
      meta.textContent = part;
      wrapper.appendChild(meta);
    });
    return wrapper;
  }

  function heading(runner) {
    return typeof runner.heading === "number" && Number.isFinite(runner.heading)
      ? runner.heading
      : null;
  }

  function markerIcon(runner, status) {
    const direction = heading(runner);
    const pointer = direction === null
      ? ""
      : '<span class="runner-marker__pointer" style="transform: rotate(' + direction + 'deg)"></span>';
    return L.divIcon({
      className: "runner-marker runner-marker--" + status,
      html: pointer + '<span class="runner-marker__number">' + runner.number + "</span>",
      iconSize: [44, 44],
      iconAnchor: [22, 22],
      popupAnchor: [0, -16],
    });
  }

  function hasPosition(runner) {
    return typeof runner.latitude === "number" && typeof runner.longitude === "number";
  }

  function isNarrow() {
    return window.matchMedia("(max-width: 899px)").matches;
  }

  function addLap(runner) {
    if (!lapForm || !lapTemplate) return;
    lapForm.action = lapTemplate.replace(
      "00000000-0000-0000-0000-000000000000",
      encodeURIComponent(runner.id)
    );
    if (lapForm.requestSubmit) lapForm.requestSubmit();
    else lapForm.submit();
  }

  function selectRunner(id, focusList) {
    selectedId = id;
    rows.forEach(function (row, rowId) {
      const selected = rowId === id;
      row.button.setAttribute("aria-pressed", selected ? "true" : "false");
      row.element.classList.toggle("is-selected", selected);
    });
    markers.forEach(function (entry, markerId) {
      entry.marker.setZIndexOffset(markerId === id ? 1000 : 0);
    });
    if (renameForm && id) {
      const runner = (snapshot.runners || []).find(function (item) { return item.id === id; });
      if (runner && renameTemplate) {
        renameInput.value = runner.name;
        renameForm.action = renameTemplate.replace(
          "00000000-0000-0000-0000-000000000000",
          encodeURIComponent(id)
        );
        renameForm.hidden = false;
      }
    } else if (renameForm) {
      renameForm.hidden = true;
    }
    if (lapForm && id) {
      const runner = (snapshot.runners || []).find(function (item) { return item.id === id; });
      if (runner && lapTemplate) {
        lapForm.action = lapTemplate.replace(
          "00000000-0000-0000-0000-000000000000",
          encodeURIComponent(id)
        );
        if (lapCount) lapCount.textContent = String(runner.laps || 0);
        lapForm.hidden = false;
      }
    } else if (lapForm) {
      lapForm.hidden = true;
    }
    if (rows.has(id) && ui().flash) ui().flash(rows.get(id).button);
    if (focusList && rows.has(id)) rows.get(id).button.focus();

    function openPopup() {
      const entry = markers.get(id);
      if (entry) entry.marker.openPopup();
    }

    if (isNarrow() && ui().setTab) {
      ui().setTab("map");
      window.setTimeout(openPopup, 80);
      return;
    }
    openPopup();
  }

  function createRow(runner) {
    const element = document.createElement("li");
    element.className = "runner-row";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "runner-row__button";
    button.setAttribute("aria-pressed", "false");
    const number = document.createElement("span");
    number.className = "runner-row__number";
    number.textContent = runner.number;
    const identity = document.createElement("span");
    identity.className = "runner-row__identity";
    const name = document.createElement("span");
    name.className = "runner-row__name";
    const meta = document.createElement("span");
    meta.className = "runner-row__meta";
    identity.appendChild(name);
    identity.appendChild(meta);
    const status = document.createElement("span");
    status.className = "runner-row__status";
    button.appendChild(number);
    button.appendChild(identity);
    button.appendChild(status);
    element.appendChild(button);
    button.addEventListener("click", function () { selectRunner(runner.id, false); });
    if (lapTemplate) {
      const lapButton = document.createElement("button");
      lapButton.type = "button";
      lapButton.className = "runner-row__lap";
      lapButton.textContent = "+1";
      lapButton.setAttribute("aria-label", "Add a lap for " + runner.name);
      lapButton.addEventListener("click", function (event) {
        event.stopPropagation();
        addLap(runner);
      });
      element.appendChild(lapButton);
    }
    listElement.appendChild(element);
    return { element: element, button: button, name: name, meta: meta, status: status, runner: runner };
  }

  function updateRow(row, runner, status) {
    row.runner = runner;
    row.name.textContent = runner.name;
    const lapText = typeof runner.laps === "number"
      ? runner.laps + (runner.laps === 1 ? " lap" : " laps")
      : "";
    const fixText = runner.position_at ? "Last fix " + clock(runner.position_at) : "No position yet";
    row.meta.textContent = lapText ? lapText + " · " + fixText : fixText;
    if (selectedId === runner.id && lapCount && typeof runner.laps === "number") {
      lapCount.textContent = String(runner.laps);
    }
    const nextStatus = statusElement(runner, status);
    row.status.replaceWith(nextStatus);
    row.status = nextStatus;
    row.button.setAttribute("aria-label", runner.number + " " + runner.name + ", " + (STATUS_LABELS[status] || status));
    row.button.setAttribute("aria-pressed", selectedId === runner.id ? "true" : "false");
    row.element.classList.toggle("is-selected", selectedId === runner.id);
  }

  function applyFilter() {
    const query = (searchElement.value || "").trim().toLocaleLowerCase();
    let visible = 0;
    rows.forEach(function (row) {
      const match = !query || row.runner.name.toLocaleLowerCase().includes(query);
      row.element.hidden = !match;
      if (match) visible += 1;
    });
    if (emptyElement) {
      emptyElement.hidden = visible !== 0;
      if (snapshot.event && snapshot.event.status === "closed" && !query) {
        emptyElement.textContent = "Event ended.";
      } else {
        emptyElement.textContent = query && visible === 0
          ? "No runners match that search."
          : "No runners on the map yet.";
      }
    }
  }

  function drawRoute(segments) {
    if (routeLayer) map.removeLayer(routeLayer);
    const all = [];
    (segments || []).forEach(function (segment) {
      segment.forEach(function (point) {
        if (Array.isArray(point) && point.length >= 2) all.push([point[0], point[1]]);
      });
    });
    if (window.RunlineRoute) {
      routeLayer = window.RunlineRoute.draw(map, segments || [], {
        color: getComputedStyle(mapElement).getPropertyValue("--color-route").trim(),
        weight: 4,
        opacity: 0.72,
        arrows: true,
        endpoints: true,
      });
      updateRouteStats(window.RunlineRoute.stats(segments || []));
    } else {
      routeLayer = L.layerGroup();
      (segments || []).forEach(function (segment) {
        const points = segment.filter(function (point) {
          return Array.isArray(point) && point.length >= 2;
        });
        if (points.length < 2) return;
        L.polyline(points, {
          color: getComputedStyle(mapElement).getPropertyValue("--color-route").trim(),
          weight: 4,
          opacity: 0.72,
          lineJoin: "round",
          lineCap: "round",
        }).addTo(routeLayer);
      });
      routeLayer.addTo(map);
    }
    if (all.length) {
      const bounds = L.latLngBounds(all);
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [28, 28], maxZoom: 15 });
    }
    mapFitted = true;
  }

  function updateRouteStats(summary) {
    if (!summary || !routeStats) return;
    const hasRoute = summary.distance > 0;
    routeStats.hidden = !hasRoute;
    if (!hasRoute) return;
    if (routeDistance) {
      routeDistance.textContent = window.RunlineRoute.formatDistance(summary.distance);
    }
    if (routeClimbTile) routeClimbTile.hidden = !summary.hasElevation;
    if (routeClimb && summary.hasElevation) {
      routeClimb.textContent = window.RunlineRoute.formatGain(summary.gain);
    }
  }

  function fitRoute() {
    const all = [];
    (snapshot.route_segments || []).forEach(function (segment) {
      segment.forEach(function (point) {
        if (Array.isArray(point) && point.length >= 2) all.push([point[0], point[1]]);
      });
    });
    if (all.length) {
      map.fitBounds(L.latLngBounds(all), { padding: [28, 28], maxZoom: 15 });
      return;
    }
    if (markers.size) {
      map.fitBounds(L.latLngBounds(Array.from(markers.values()).map(function (entry) {
        return entry.marker.getLatLng();
      })), { padding: [40, 40], maxZoom: 15 });
    }
  }

  function renderMap(now) {
    if (!map) return;
    const runners = snapshot.runners || [];
    const activeIds = new Set();
    runners.forEach(function (runner) {
      const status = effectiveStatus(runner, now);
      if (!hasPosition(runner) || (status !== "live" && status !== "stale")) return;
      activeIds.add(runner.id);
      const latlng = [runner.latitude, runner.longitude];
      const popup = popupContent(runner, status);
      let entry = markers.get(runner.id);
      if (!entry) {
        const marker = L.marker(latlng, {
          icon: markerIcon(runner, status),
          title: runner.name,
          alt: runner.name,
          keyboard: true,
        }).addTo(map);
        marker.on("click", function () { selectRunner(runner.id, false); });
        marker.bindPopup(popup);
        entry = {
          marker: marker,
          markerKey: runner.number + status + heading(runner),
          popupKey: runner.name + status,
        };
        markers.set(runner.id, entry);
      } else {
        entry.marker.setLatLng(latlng);
        const markerKey = runner.number + status + heading(runner);
        if (entry.markerKey !== markerKey) {
          entry.marker.setIcon(markerIcon(runner, status));
          entry.markerKey = markerKey;
        }
        const popupKey = runner.name + status;
        if (entry.popupKey !== popupKey) {
          entry.marker.setPopupContent(popup);
          entry.popupKey = popupKey;
        }
      }
      entry.marker.setZIndexOffset(selectedId === runner.id ? 1000 : 0);
    });
    markers.forEach(function (entry, id) {
      if (!activeIds.has(id)) {
        entry.marker.remove();
        markers.delete(id);
      }
    });
  }

  function renderList(now) {
    const runners = snapshot.runners || [];
    const activeIds = new Set();
    runners.forEach(function (runner) {
      activeIds.add(runner.id);
      const status = effectiveStatus(runner, now);
      let row = rows.get(runner.id);
      if (!row) {
        row = createRow(runner);
        rows.set(runner.id, row);
      }
      updateRow(row, runner, status);
      listElement.appendChild(row.element);
    });
    rows.forEach(function (row, id) {
      if (!activeIds.has(id)) {
        row.element.remove();
        rows.delete(id);
      }
    });
    if (selectedId && !activeIds.has(selectedId)) {
      selectedId = null;
      if (renameForm) renameForm.hidden = true;
    }
    applyFilter();
  }

  function renderSummary(now) {
    const runners = snapshot.runners || [];
    let live = 0;
    let stale = 0;
    runners.forEach(function (runner) {
      const status = effectiveStatus(runner, now);
      if (status === "live") live += 1;
      if (status === "stale") stale += 1;
    });
    summary.participants.textContent = String(runners.length);
    summary.live.textContent = String(live);
    summary.stale.textContent = String(stale);
    if (summary.liveTile) summary.liveTile.dataset.tone = live ? "live" : "";
    if (summary.staleTile) summary.staleTile.dataset.tone = stale ? "stale" : "";
    const offline = connectionState === "offline";
    if (liveChipCount) liveChipCount.textContent = offline ? "" : String(live);
    if (liveChipLabel) liveChipLabel.textContent = offline ? "Offline" : "live";
    if (liveChip) {
      const closed = Boolean(snapshot.event && snapshot.event.status === "closed");
      liveChip.hidden = runners.length === 0 || closed;
      liveChip.dataset.state = closed
        ? "ended"
        : connectionState === "offline"
          ? "offline"
          : live > 0
            ? "live"
            : "stale";
    }
    if (runnerBadge) {
      runnerBadge.textContent = String(runners.length);
      runnerBadge.hidden = runners.length === 0;
    }
  }

  function render() {
    const now = serverNow();
    const signature = JSON.stringify(snapshot.route_segments || []);
    if (map && signature !== routeSignature) {
      routeSignature = signature;
      drawRoute(snapshot.route_segments || []);
    }
    renderMap(now);
    renderList(now);
    renderSummary(now);
    lastRefresh.textContent = clock(lastSuccessfulAt.toISOString());
    lastRefresh.dateTime = lastSuccessfulAt.toISOString();
    if (snapshot.event && snapshot.event.status === "closed") {
      setConnection("Event closed. No runner positions are shown.", "ended");
    }
  }

  function clearVisibleData() {
    markers.forEach(function (entry) { entry.marker.remove(); });
    markers = new Map();
    rows.forEach(function (row) { row.element.remove(); });
    rows = new Map();
    selectedId = null;
    if (renameForm) renameForm.hidden = true;
    if (lapForm) lapForm.hidden = true;
    summary.participants.textContent = "0";
    summary.live.textContent = "0";
    summary.stale.textContent = "0";
    if (emptyElement) emptyElement.hidden = false;
  }

  function stopPolling() {
    stopped = true;
    if (timer) window.clearTimeout(timer);
    timer = null;
  }

  function schedule() {
    if (stopped) return;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(poll, POLL_MS);
  }

  async function poll() {
    if (stopped) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(function () { controller.abort(); }, 15000);
    try {
      const response = await fetch(snapshotUrl, {
        cache: "no-store",
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403) {
        stopPolling();
        clearVisibleData();
        setConnection(mode === "staff" ? "Session expired. Sign in again." : "Access denied.", "ended");
        return;
      }
      if (response.status === 404) {
        stopPolling();
        clearVisibleData();
        setConnection("This map is no longer available.", "ended");
        return;
      }
      if (!response.ok) throw new Error("Server error");
      const data = await response.json();
      snapshot = data;
      lastServerTime = Date.parse(data.server_time) || Date.now();
      lastFetchAt = Date.now();
      lastSuccessfulAt = new Date();
      setConnection("Connected · updated " + clock(lastSuccessfulAt.toISOString()), "ok");
      render();
      if (data.event && data.event.status === "closed") {
        stopPolling();
      }
    } catch (error) {
      if (!stopped) {
        setConnection("Offline · showing the last successful update " + clock(lastSuccessfulAt.toISOString()), "offline");
        render();
      }
    } finally {
      window.clearTimeout(timeout);
      if (!stopped) schedule();
    }
  }

  function init() {
    if (searchElement) searchElement.addEventListener("input", applyFilter);
    document.addEventListener("runline:tab", function (event) {
      if (!map || !event.detail || event.detail.tab !== "map") return;
      window.requestAnimationFrame(function () { map.invalidateSize(); });
    });
    if (!window.L) {
      setConnection("Map library failed to load. The runner list is still available.", "offline");
      render();
      return;
    }
    map = L.map(mapElement, { zoomControl: true, attributionControl: true, fadeAnimation: false }).setView([20, 0], 2);
    tileLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    tileLayer.on("tileerror", function () {
      if (mapWarning) mapWarning.hidden = false;
    });
    tileLayer.on("tileload", function () {
      if (mapWarning) mapWarning.hidden = true;
    });
    if (fitButton) fitButton.addEventListener("click", fitRoute);
    if (snapshot.runners && snapshot.runners.length === 1) {
      selectedId = snapshot.runners[0].id;
    }
    setConnection(
      snapshot.event && snapshot.event.status === "closed"
        ? "Event closed. No runner positions are shown."
        : "Connected · last refresh " + clock(snapshot.server_time),
      snapshot.event && snapshot.event.status === "closed" ? "ended" : "ok"
    );
    render();
    if (snapshot.event && snapshot.event.status === "closed") {
      stopPolling();
      return;
    }
    schedule();
  }

  init();
})();
