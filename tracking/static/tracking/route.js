(function () {
  "use strict";

  const EARTH_RADIUS_M = 6371008.8;
  const MAX_ARROWS = 16;
  const MIN_ARROW_SPACING_M = 150;
  const ELEVATION_STOPS = [
    [0, [43, 108, 176]],
    [0.45, [56, 161, 105]],
    [0.72, [214, 158, 46]],
    [1, [197, 48, 48]],
  ];

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

  function elevationColor(fraction) {
    const t = Math.min(1, Math.max(0, fraction));
    for (let index = 1; index < ELEVATION_STOPS.length; index += 1) {
      const [end, endColor] = ELEVATION_STOPS[index];
      const [start, startColor] = ELEVATION_STOPS[index - 1];
      if (t > end) continue;
      const mix = end === start ? 0 : (t - start) / (end - start);
      return "rgb("
        + Math.round(startColor[0] + (endColor[0] - startColor[0]) * mix) + ", "
        + Math.round(startColor[1] + (endColor[1] - startColor[1]) * mix) + ", "
        + Math.round(startColor[2] + (endColor[2] - startColor[2]) * mix) + ")";
    }
    const last = ELEVATION_STOPS[ELEVATION_STOPS.length - 1][1];
    return "rgb(" + last.join(", ") + ")";
  }

  function profile(segments) {
    const clean = cleanSegments(segments);
    const lengths = clean.map(function (segment) {
      let length = 0;
      for (let index = 1; index < segment.length; index += 1) {
        length += haversine(segment[index - 1], segment[index]);
      }
      return length;
    });
    const distance = lengths.reduce(function (sum, length) { return sum + length; }, 0);
    const hasElevation = clean.some(function (segment) {
      return segment.some(function (point) { return point[2] !== null; });
    });
    if (!hasElevation || distance <= 0) {
      return {
        distance: distance,
        hasElevation: false,
        maxElevation: 0,
        minElevation: 0,
        samples: [],
        segments: [],
      };
    }
    const step = Math.max(distance / 220, 10);
    const samples = [];
    const sampledSegments = [];
    let traveled = 0;
    clean.forEach(function (segment, segmentIndex) {
      const cumulative = [0];
      for (let index = 1; index < segment.length; index += 1) {
        cumulative.push(cumulative[index - 1] + haversine(segment[index - 1], segment[index]));
      }
      const length = lengths[segmentIndex];
      if (length <= 0) return;
      const count = Math.max(1, Math.ceil(length / step));
      const segmentSamples = [];
      let cursor = 0;
      for (let index = 0; index <= count; index += 1) {
        const target = length * index / count;
        while (cursor < cumulative.length - 2 && cumulative[cursor + 1] < target) {
          cursor += 1;
        }
        const start = segment[cursor];
        const end = segment[cursor + 1];
        const startDistance = cumulative[cursor];
        const endDistance = cumulative[cursor + 1];
        const fraction = endDistance > startDistance
          ? (target - startDistance) / (endDistance - startDistance)
          : 0;
        const elevation = start[2] !== null && end[2] !== null
          ? start[2] + (end[2] - start[2]) * fraction
          : null;
        const sample = {
          lat: start[0] + (end[0] - start[0]) * fraction,
          lng: start[1] + (end[1] - start[1]) * fraction,
          elevation: elevation,
          distance: traveled + target,
        };
        segmentSamples.push(sample);
        samples.push(sample);
      }
      sampledSegments.push(segmentSamples);
      traveled += length;
    });
    const elevations = samples
      .filter(function (sample) { return sample.elevation !== null; })
      .map(function (sample) { return sample.elevation; });
    return {
      distance: distance,
      hasElevation: elevations.length > 0,
      maxElevation: Math.max.apply(null, elevations),
      minElevation: Math.min.apply(null, elevations),
      samples: samples,
      segments: sampledSegments,
    };
  }

  function pointAt(data, fraction) {
    if (!data || !data.samples || !data.samples.length || data.distance <= 0) return null;
    const target = Math.min(1, Math.max(0, fraction)) * data.distance;
    let low = 0;
    let high = data.samples.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (data.samples[middle].distance < target) low = middle + 1;
      else high = middle;
    }
    const next = data.samples[low];
    if (low === 0 || next.distance <= target) {
      return {
        lat: next.lat,
        lng: next.lng,
        elevation: next.elevation,
        distance: next.distance,
      };
    }
    const previous = data.samples[low - 1];
    const span = next.distance - previous.distance;
    const mix = span > 0 ? (target - previous.distance) / span : 0;
    return {
      lat: previous.lat + (next.lat - previous.lat) * mix,
      lng: previous.lng + (next.lng - previous.lng) * mix,
      elevation: previous.elevation !== null && next.elevation !== null
        ? previous.elevation + (next.elevation - previous.elevation) * mix
        : (previous.elevation === null ? next.elevation : previous.elevation),
      distance: target,
    };
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
    const lineOptions = {
      weight: options.weight || 4,
      opacity: options.opacity === undefined ? 0.72 : options.opacity,
      dashArray: options.dashArray || null,
      lineJoin: "round",
      lineCap: "round",
    };
    const gradient = options.gradient ? profile(segments) : null;
    if (gradient && gradient.hasElevation && gradient.maxElevation > gradient.minElevation) {
      const span = gradient.maxElevation - gradient.minElevation;
      gradient.segments.forEach(function (samples) {
        for (let index = 1; index < samples.length; index += 1) {
          const start = samples[index - 1];
          const end = samples[index];
          const elevation = start.elevation !== null && end.elevation !== null
            ? (start.elevation + end.elevation) / 2
            : (start.elevation === null ? end.elevation : start.elevation);
          const color = elevation === null
            ? options.color
            : elevationColor((elevation - gradient.minElevation) / span);
          L.polyline(
            [[start.lat, start.lng], [end.lat, end.lng]],
            Object.assign({}, lineOptions, { color: color })
          ).addTo(group);
        }
      });
    } else {
      clean.forEach(function (segment) {
        L.polyline(
          segment.map(function (point) { return [point[0], point[1]]; }),
          Object.assign({}, lineOptions, { color: options.color })
        ).addTo(group);
      });
    }
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
    elevationColor: elevationColor,
    formatDistance: formatDistance,
    formatGain: formatGain,
    haversine: haversine,
    pointAt: pointAt,
    profile: profile,
    stats: stats,
  };
})();
