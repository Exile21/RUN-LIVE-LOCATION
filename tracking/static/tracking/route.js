(function () {
  "use strict";

  const EARTH_RADIUS_M = 6371008.8;
  const MAX_ARROWS = 16;
  const MIN_ARROW_SPACING_M = 150;

  function cleanSegments(segments) {
    if (!Array.isArray(segments)) return [];
    return segments
      .map(function (segment) {
        if (!Array.isArray(segment)) return [];
        return segment
          .filter(function (point) {
            return Array.isArray(point)
              && point.length >= 2
              && Number.isFinite(point[0])
              && Number.isFinite(point[1]);
          })
          .map(function (point) {
            return [
              point[0],
              point[1],
              Number.isFinite(point[2]) ? point[2] : null,
            ];
          });
      })
      .filter(function (segment) { return segment.length >= 2; });
  }

  function haversine(a, b) {
    const latitude = (b[0] - a[0]) * Math.PI / 180;
    const longitude = (b[1] - a[1]) * Math.PI / 180;
    const lat1 = a[0] * Math.PI / 180;
    const lat2 = b[0] * Math.PI / 180;
    const h = Math.sin(latitude / 2) * Math.sin(latitude / 2)
      + Math.cos(lat1) * Math.cos(lat2) * Math.sin(longitude / 2) * Math.sin(longitude / 2);
    return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function bearing(a, b) {
    const lat1 = a[0] * Math.PI / 180;
    const lat2 = b[0] * Math.PI / 180;
    const longitude = (b[1] - a[1]) * Math.PI / 180;
    const y = Math.sin(longitude) * Math.cos(lat2);
    const x = Math.cos(lat1) * Math.sin(lat2)
      - Math.sin(lat1) * Math.cos(lat2) * Math.cos(longitude);
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
  }

  function stats(segments) {
    let distance = 0;
    let gain = 0;
    let hasElevation = false;
    cleanSegments(segments).forEach(function (segment) {
      let previous = null;
      let previousElevation = null;
      segment.forEach(function (point) {
        if (previous) distance += haversine(previous, point);
        if (point[2] === null) {
          previousElevation = null;
        } else {
          hasElevation = true;
          // shortcut: raw positive ascent, smooth noisy GPS elevation before comparing for stricter climb totals.
          if (previousElevation !== null && point[2] > previousElevation) {
            gain += point[2] - previousElevation;
          }
          previousElevation = point[2];
        }
        previous = point;
      });
    });
    return { distance: distance, gain: gain, hasElevation: hasElevation };
  }

  function arrows(segments) {
    const clean = cleanSegments(segments);
    const lengths = clean.map(function (segment) {
      return segment.slice(1).map(function (point, index) {
        return haversine(segment[index], point);
      });
    });
    const total = lengths.reduce(function (sum, segment) {
      return sum + segment.reduce(function (part, length) { return part + length; }, 0);
    }, 0);
    if (total <= 0) return [];
    const spacing = Math.max(total / MAX_ARROWS, MIN_ARROW_SPACING_M);
    const targets = [];
    for (let at = Math.min(spacing, total / 2); at < total; at += spacing) {
      targets.push(at);
    }
    const placed = [];
    let walked = 0;
    let target = 0;
    clean.forEach(function (segment, segmentIndex) {
      segment.slice(1).forEach(function (end, index) {
        const start = segment[index];
        const length = lengths[segmentIndex][index];
        if (length <= 0) return;
        while (target < targets.length && walked + length >= targets[target]) {
          const fraction = (targets[target] - walked) / length;
          placed.push({
            lat: start[0] + (end[0] - start[0]) * fraction,
            lng: start[1] + (end[1] - start[1]) * fraction,
            angle: bearing(start, end),
          });
          target += 1;
        }
        walked += length;
      });
    });
    return placed;
  }

  function endpoint(group, point, kind, direction) {
    L.marker([point[0], point[1]], {
      icon: L.divIcon({
        className: "route-endpoint route-endpoint--" + kind,
        html: "",
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      }),
      interactive: false,
      keyboard: false,
    })
      .bindTooltip(kind === "start" ? "Start" : "Finish", {
        permanent: true,
        direction: direction,
        className: "route-endpoint-tip",
        offset: [0, direction === "top" ? -10 : 10],
      })
      .addTo(group);
  }

  function draw(map, segments, options) {
    options = options || {};
    const clean = cleanSegments(segments);
    const group = L.layerGroup();
    clean.forEach(function (segment) {
      L.polyline(
        segment.map(function (point) { return [point[0], point[1]]; }),
        {
          color: options.color,
          weight: options.weight || 4,
          opacity: options.opacity === undefined ? 0.72 : options.opacity,
          dashArray: options.dashArray || null,
          lineJoin: "round",
          lineCap: "round",
        }
      ).addTo(group);
    });
    if (options.arrows !== false) {
      arrows(segments).forEach(function (point) {
        L.marker([point.lat, point.lng], {
          icon: L.divIcon({
            className: "route-arrow-icon",
            html: '<span class="route-arrow" style="transform: rotate('
              + point.angle.toFixed(1)
              + 'deg)"><svg viewBox="0 0 24 24" aria-hidden="true">'
              + '<path d="M6.5 16 12 9.5 17.5 16" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"></path>'
              + '<path d="M6.5 16 12 9.5 17.5 16" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"></path>'
              + "</svg></span>",
            iconSize: [22, 22],
            iconAnchor: [11, 11],
          }),
          interactive: false,
          keyboard: false,
        }).addTo(group);
      });
    }
    if (options.endpoints !== false && clean.length) {
      endpoint(group, clean[0][0], "start", "top");
      endpoint(group, clean[clean.length - 1].slice(-1)[0], "finish", "bottom");
    }
    group.addTo(map);
    return group;
  }

  function formatDistance(meters) {
    if (!Number.isFinite(meters) || meters <= 0) return "0 m";
    if (meters < 1000) return Math.round(meters / 10) * 10 + " m";
    return (meters / 1000).toFixed(meters >= 100000 ? 0 : 1) + " km";
  }

  function formatGain(meters) {
    return Math.round(meters) + " m";
  }

  window.RunlineRoute = {
    arrows: arrows,
    bearing: bearing,
    draw: draw,
    formatDistance: formatDistance,
    formatGain: formatGain,
    haversine: haversine,
    stats: stats,
  };
})();
