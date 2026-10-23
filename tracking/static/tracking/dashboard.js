/* Live elapsed time for current runner events. */
(function () {
  "use strict";

  const root = document.querySelector("[data-server-now]");
  const elements = Array.from(document.querySelectorAll("[data-elapsed]"));
  if (!root || !elements.length) return;

  const serverNow = Date.parse(root.dataset.serverNow);
  const offset = Number.isNaN(serverNow) ? 0 : serverNow - Date.now();

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function format(milliseconds) {
    const total = Math.max(0, Math.floor(milliseconds / 1000));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    return pad(hours) + ":" + pad(minutes) + ":" + pad(seconds);
  }

  function paint() {
    const now = Date.now() + offset;
    elements.forEach(function (element) {
      const startsAt = Date.parse(element.dataset.elapsed);
      if (!Number.isNaN(startsAt)) element.textContent = format(now - startsAt);
    });
  }

  window.RunlineElapsed = { format: format };
  paint();
  window.setInterval(paint, 1000);
})();
