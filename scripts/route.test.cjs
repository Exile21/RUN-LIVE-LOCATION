const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "../tracking/static/tracking/route.js"), "utf8");

function setup(L) {
  const window = {};
  runInNewContext(source, { window, Math, L });
  return window.RunlineRoute;
}

function leaflet() {
  const map = {};
  const L = {
    layerGroup() {
      return {
        layers: [],
        addTo(target) { target.group = this; return this; },
      };
    },
    polyline(points, options) {
      return {
        kind: "polyline",
        points,
        options,
        addTo(group) { group.layers.push(this); return this; },
      };
    },
    marker(latLng, options) {
      return {
        kind: "marker",
        latLng,
        options,
        tooltip: null,
        addTo(group) { group.layers.push(this); return this; },
        bindTooltip(text, tooltip) { this.tooltip = { text, ...tooltip }; return this; },
      };
    },
    divIcon(options) { return options; },
  };
  return { map, L };
}

test("stats measure distance, climb, and missing elevation", () => {
  const route = setup();
  const measured = route.stats([[
    [0, 0, 10],
    [0, 0.001, 20],
    [0, 0.002, 15],
    [0, 0.003, 30],
  ]]);
  assert.ok(measured.distance > 330 && measured.distance < 335);
  assert.equal(measured.gain, 25);
  assert.equal(measured.hasElevation, true);

  const flat = route.stats([[[0, 0], [0, 0.001]]]);
  assert.ok(flat.distance > 100);
  assert.equal(flat.gain, 0);
  assert.equal(flat.hasElevation, false);
});

test("arrows are placed along the route in the direction of travel", () => {
  const route = setup();
  const east = route.arrows([[[0, 0], [0, 0.002]]]);
  assert.equal(east.length, 1);
  assert.ok(Math.abs(east[0].angle - 90) < 0.01);
  assert.ok(Math.abs(east[0].lat) < 1e-9);
  assert.ok(Math.abs(east[0].lng - 0.001) < 1e-9);

  const long = route.arrows([[[0, 0], [0, 0.1]]]);
  assert.ok(long.length > 1 && long.length <= 16);
  assert.equal(route.arrows([]).length, 0);
});

test("draw adds the route line, arrows, and labelled endpoints", () => {
  const { map, L } = leaflet();
  const route = setup(L);

  const group = route.draw(map, [[[0, 0], [0, 0.002]]], {
    color: "#333",
    arrows: true,
    endpoints: true,
  });
  const polylines = group.layers.filter((layer) => layer.kind === "polyline");
  const markers = group.layers.filter((layer) => layer.kind === "marker");
  assert.equal(polylines.length, 1);
  assert.ok(markers.some((marker) => marker.tooltip && marker.tooltip.text === "Start"));
  assert.ok(markers.some((marker) => marker.tooltip && marker.tooltip.text === "Finish"));
  assert.ok(markers.some((marker) => /rotate\(/.test(marker.options.icon.html)));
});

test("distance and climb formatting stay compact", () => {
  const route = setup();
  assert.equal(route.formatDistance(0), "0 m");
  assert.equal(route.formatDistance(854), "850 m");
  assert.equal(route.formatDistance(5240), "5.2 km");
  assert.equal(route.formatDistance(42195), "42.2 km");
  assert.equal(route.formatGain(123.6), "124 m");
});
