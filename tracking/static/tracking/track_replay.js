(function () {
  "use strict";

  const mapElement = document.getElementById("track-map");
  const dataElement = document.getElementById("track-data");
  const slider = document.getElementById("track-slider");
  const playButton = document.getElementById("track-play");
  const timeElement = document.getElementById("track-time");
  const pointLabel = document.getElementById("track-point-label");
  if (!mapElement || !dataElement || !slider) return;

  const track = JSON.parse(dataElement.textContent);
  const points = track.points || [];
  if (!points.length) return;

  const map = L.map(mapElement, { scrollWheelZoom: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  const routeColor = getComputedStyle(mapElement).getPropertyValue("--color-route").trim();
  const trackColor = getComputedStyle(mapElement).getPropertyValue("--color-accent-2").trim();
  const bounds = L.latLngBounds([]);

  const routeModule = typeof window !== "undefined" ? window.RunlineRoute : null;
  if (routeModule) {
    routeModule.draw(map, track.route || [], {
      color: routeColor,
      weight: 4,
      opacity: 0.4,
      dashArray: "6 8",
      arrows: true,
      endpoints: true,
    });
  } else {
    (track.route || []).forEach(function (segment) {
      const route = (segment || []).filter(function (point) {
        return Array.isArray(point) && point.length >= 2;
      });
      if (route.length < 2) return;
      L.polyline(route, {
        color: routeColor,
        weight: 4,
        opacity: 0.4,
        dashArray: "6 8",
        lineJoin: "round",
      }).addTo(map);
    });
  }
  (track.route || []).forEach(function (segment) {
    (segment || []).forEach(function (point) {
      if (Array.isArray(point) && point.length >= 2) bounds.extend(point);
    });
  });

  L.polyline(
    points.map(function (point) { return [point.lat, point.lon]; }),
    { color: trackColor, weight: 5, lineJoin: "round", lineCap: "round" }
  ).addTo(map);
  points.forEach(function (point) { bounds.extend([point.lat, point.lon]); });

  function dot(point, color) {
    return L.circleMarker([point.lat, point.lon], {
      radius: 6,
      color: "#ffffff",
      weight: 2,
      fillColor: color,
      fillOpacity: 1,
    }).addTo(map);
  }

  dot(points[0], "#2f7d4f");
  dot(points[points.length - 1], "#30343b");

  const cursor = L.circleMarker([points[0].lat, points[0].lon], {
    radius: 8,
    color: "#ffffff",
    weight: 2,
    fillColor: trackColor,
    fillOpacity: 1,
  }).addTo(map);

  function formatTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Time unavailable";
    return date.toLocaleString([], {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function show(index) {
    const point = points[index];
    cursor.setLatLng([point.lat, point.lon]);
    if (timeElement) timeElement.textContent = formatTime(point.at);
    if (pointLabel) {
      let label = "Point " + (index + 1) + " of " + points.length;
      if (point.accuracy != null) label += " · ±" + Math.round(point.accuracy) + " m";
      pointLabel.textContent = label;
    }
  }

  slider.addEventListener("input", function () {
    stopPlayback();
    show(Number(slider.value));
  });

  let timer = null;
  function stopPlayback() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    if (playButton) playButton.textContent = "Play";
  }

  if (playButton) {
    playButton.addEventListener("click", function () {
      if (timer) {
        stopPlayback();
        return;
      }
      if (Number(slider.value) >= points.length - 1) {
        slider.value = "0";
        show(0);
      }
      playButton.textContent = "Pause";
      timer = setInterval(function () {
        const next = Number(slider.value) + 1;
        if (next >= points.length) {
          stopPlayback();
          return;
        }
        slider.value = String(next);
        show(next);
        if (next >= points.length - 1) stopPlayback();
      }, 150);
    });
  }

  show(points.length - 1);
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [28, 28], maxZoom: 16 });
  else map.setView([points[0].lat, points[0].lon], 15);
})();
