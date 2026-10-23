const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "../tracking/static/tracking/track_replay.js"), "utf8");

function setup(points) {
  const listeners = {};
  const markers = [];
  let intervalCallback = null;

  const elements = {
    "track-map": {},
    "track-data": { textContent: JSON.stringify({ points, route: [] }) },
    "track-slider": {
      value: String(points.length - 1),
      addEventListener(type, callback) { listeners[type] = callback; },
    },
    "track-play": {
      textContent: "Play",
      addEventListener(type, callback) { listeners[type] = callback; },
    },
    "track-time": { textContent: "" },
    "track-point-label": { textContent: "" },
  };

  const map = { fitBounds() {}, setView() {} };
  const L = {
    map() { return map; },
    tileLayer() { return { addTo() {} }; },
    polyline() { return { addTo() {} }; },
    latLngBounds() { return { extend() {}, isValid() { return true; } }; },
    circleMarker(latLng) {
      const marker = { latLng, setLatLng(value) { this.latLng = value; } };
      markers.push(marker);
      return { addTo() { return marker; } };
    },
  };

  runInNewContext(source, {
    document: { getElementById(id) { return elements[id] || null; } },
    getComputedStyle() { return { getPropertyValue() { return ""; } }; },
    L,
    setInterval(callback) { intervalCallback = callback; return 1; },
    clearInterval() {},
  });

  return {
    elements,
    listeners,
    cursor: markers[2],
    tick() { intervalCallback(); },
  };
}

test("slider and playback move the cursor through recorded points", () => {
  const points = [
    { lat: 1.0, lon: 2.0, at: "2026-10-23T10:00:00+07:00" },
    { lat: 1.1, lon: 2.1, at: "2026-10-23T10:01:00+07:00" },
    { lat: 1.2, lon: 2.2, at: "2026-10-23T10:02:00+07:00" },
  ];
  const state = setup(points);

  assert.equal(String(state.cursor.latLng), "1.2,2.2");
  assert.equal(state.elements["track-point-label"].textContent, "Point 3 of 3");

  state.elements["track-slider"].value = "1";
  state.listeners.input();
  assert.equal(String(state.cursor.latLng), "1.1,2.1");
  assert.equal(state.elements["track-point-label"].textContent, "Point 2 of 3");

  state.listeners.click();
  assert.equal(state.elements["track-play"].textContent, "Pause");
  state.tick();
  assert.equal(String(state.cursor.latLng), "1.2,2.2");
  assert.equal(state.elements["track-point-label"].textContent, "Point 3 of 3");
  assert.equal(state.elements["track-play"].textContent, "Play");
});
