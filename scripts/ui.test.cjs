const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "../tracking/static/tracking/ui.js"), "utf8");

function setup({ reduced = false, animation = true, tab = "map", desktop = false } = {}) {
  const listeners = {};
  const animations = [];
  const events = [];
  const panels = {};
  const classes = new Set();
  const items = ["map", "runners", "info"].map((name) => ({
    dataset: { tab: name },
    attributes: { "aria-controls": "panel-" + name },
    setAttribute(key, value) { this.attributes[key] = value; },
    getAttribute(key) { return this.attributes[key]; },
    focus() { document.activeElement = this; },
  }));
  items.forEach((item) => { panels[item.attributes["aria-controls"]] = {}; });
  const shell = { dataset: { tab: "map" } };
  const tabbar = {
    querySelectorAll() { return items; },
    querySelector() { return items.find((item) => item.attributes["aria-selected"] === "true"); },
    addEventListener(type, callback) { listeners[type] = callback; },
  };
  const document = {
    activeElement: items[0],
    documentElement: { classList: { add(name) { classes.add(name); } } },
    querySelector(selector) { return selector === ".map-shell" ? shell : tabbar; },
    getElementById(id) { return panels[id]; },
    addEventListener() {},
    dispatchEvent(event) { events.push(event); },
  };
  const window = {
    location: { href: "http://localhost/events/1/?other=kept&tab=" + encodeURIComponent(tab) },
    matchMedia(query) {
      return { matches: query.includes("reduced") ? reduced : desktop, addEventListener() {} };
    },
    history: { replaceState(_state, _title, url) { window.location.href = String(url); } },
    addEventListener() {},
  };
  if (animation) window.anime = { animate(target, properties) { animations.push({ target, properties }); } };
  runInNewContext(source, { window, document, URL, CustomEvent: class {
    constructor(type, options) { this.type = type; this.detail = options.detail; }
  } });
  return { window, document, shell, items, listeners, animations, events, classes };
}

test("URL restores the tab without an entrance animation", () => {
  const state = setup({ tab: "runners" });
  assert.equal(state.shell.dataset.tab, "runners");
  assert.equal(state.items[1].attributes.tabindex, "0");
  assert.equal(state.items[0].attributes.tabindex, "-1");
  assert.equal(state.animations.length, 0);
  assert.ok(state.classes.has("js"));
});

test("tabs work when Anime.js is unavailable and retain unrelated URL parameters", () => {
  const state = setup({ animation: false });
  state.window.RunlineUI.setTab("info");
  assert.equal(state.shell.dataset.tab, "info");
  const url = new URL(state.window.location.href);
  assert.equal(url.searchParams.get("other"), "kept");
  assert.equal(url.searchParams.get("tab"), "info");
  assert.equal(state.items.filter((item) => item.attributes.tabindex === "0").length, 1);
  state.window.RunlineUI.setTab('bad"]selector');
  assert.equal(state.shell.dataset.tab, "info");
});

test("an unknown URL tab restores the map with one keyboard tab stop", () => {
  const state = setup({ tab: 'unknown"]selector' });
  assert.equal(state.shell.dataset.tab, "map");
  assert.equal(state.items[0].attributes.tabindex, "0");
  assert.equal(state.items.filter((item) => item.attributes.tabindex === "0").length, 1);
});

test("reduced motion prevents both tab and selection animations", () => {
  const state = setup({ reduced: true });
  state.window.RunlineUI.setTab("info");
  state.window.RunlineUI.flash(state.items[0]);
  assert.equal(state.shell.dataset.tab, "info");
  assert.equal(state.animations.length, 0);
});

test("tab changes use Anime.js only after a real state change", () => {
  const state = setup();
  state.window.RunlineUI.setTab("info");
  state.window.RunlineUI.setTab("info");
  assert.equal(state.animations.length, 1);
  assert.equal(state.animations[0].properties.ease, "out(3)");
  assert.equal(state.animations[0].properties.duration, 180);
  assert.equal(state.events.at(-1).type, "runline:tab");
});

test("keyboard arrows wrap and Home/End select the correct tab", () => {
  const state = setup({ animation: false });
  let prevented = 0;
  function key(name) { state.listeners.keydown({ key: name, preventDefault() { prevented++; } }); }
  key("ArrowLeft");
  assert.equal(state.shell.dataset.tab, "info");
  key("Home");
  assert.equal(state.shell.dataset.tab, "map");
  key("End");
  assert.equal(state.shell.dataset.tab, "info");
  key("ArrowRight");
  assert.equal(state.shell.dataset.tab, "map");
  key("Tab");
  assert.equal(prevented, 4);
});
