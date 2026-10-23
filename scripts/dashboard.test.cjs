const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "../tracking/static/tracking/dashboard.js"), "utf8");

function setup(serverNow, startsAt) {
  const element = {
    dataset: { elapsed: startsAt },
    textContent: "",
  };
  const root = { dataset: { serverNow } };
  const document = {
    querySelector(selector) { return selector === "[data-server-now]" ? root : null; },
    querySelectorAll() { return [element]; },
  };
  const window = {
    setInterval() {},
  };
  runInNewContext(source, { window, document, Date });
  return { element, elapsed: window.RunlineElapsed };
}

test("elapsed time uses the server clock instead of the browser clock", () => {
  const now = "2026-10-23T10:00:00+00:00";
  const state = setup(now, "2026-10-23T09:59:30+00:00");
  assert.equal(state.elapsed.format(3723000), "01:02:03");
  assert.match(state.element.textContent, /^00:00:30$/);
});

test("format clamps negative durations to zero", () => {
  const state = setup("2026-10-23T10:00:00+00:00", "2026-10-23T10:00:00+00:00");
  assert.equal(state.elapsed.format(-5000), "00:00:00");
});
