const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "../tracking/static/tracking/events.js"), "utf8");

function setup() {
  const listeners = {};
  const cards = ["Summer 10K", "Winter 5K"].map((name, index) => ({
    dataset: { name: name.toLowerCase(), status: index ? "draft" : "open" },
    hidden: false,
  }));
  const chips = ["", "open", "draft"].map((status) => {
    const chip = {
      dataset: { eventStatus: status },
      attributes: { "aria-pressed": status ? "false" : "true" },
      classes: new Set(status ? [] : ["is-active"]),
      listener: null,
      classList: null,
      setAttribute(key, value) { this.attributes[key] = value; },
      addEventListener(type, callback) { this.listener = callback; },
    };
    chip.classList = {
      toggle(name, on) { chip.classes[on ? "add" : "delete"](name); },
    };
    return chip;
  });
  const search = {
    value: "",
    addEventListener(type, callback) { listeners[type] = callback; },
  };
  const empty = { hidden: true };
  const document = {
    getElementById(id) { return id === "event-search" ? search : id === "event-filter-empty" ? empty : null; },
    querySelectorAll(selector) {
      if (selector.startsWith(".event-card")) return cards;
      return selector === "[data-event-status]" ? chips : [];
    },
  };
  runInNewContext(source, { document });
  return { cards, chips, search, empty, listeners };
}

test("search hides cards without a matching name and restores them when cleared", () => {
  const state = setup();
  state.search.value = "10k";
  state.listeners.input();
  assert.deepEqual(state.cards.map((card) => card.hidden), [false, true]);
  assert.equal(state.empty.hidden, true);

  state.search.value = "nothing";
  state.listeners.input();
  assert.deepEqual(state.cards.map((card) => card.hidden), [true, true]);
  assert.equal(state.empty.hidden, false);

  state.search.value = "";
  state.listeners.input();
  assert.deepEqual(state.cards.map((card) => card.hidden), [false, false]);
  assert.equal(state.empty.hidden, true);
});

test("status chips combine with the current search and expose the active state", () => {
  const state = setup();
  state.search.value = "winter";
  state.listeners.input();

  state.chips[2].listener();
  assert.deepEqual(state.cards.map((card) => card.hidden), [true, false]);
  assert.equal(state.chips[2].attributes["aria-pressed"], "true");
  assert.equal(state.chips[0].attributes["aria-pressed"], "false");
  assert.ok(state.chips[2].classes.has("is-active"));
  assert.ok(!state.chips[0].classes.has("is-active"));

  state.chips[0].listener();
  assert.deepEqual(state.cards.map((card) => card.hidden), [true, false]);
  assert.equal(state.empty.hidden, true);
});
