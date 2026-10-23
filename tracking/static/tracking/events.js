/* Client-side event search and status filters; the full list remains in the HTML. */
(function () {
  "use strict";

  const search = document.getElementById("event-search");
  const cards = Array.from(document.querySelectorAll(".event-card[data-status]"));
  const chips = Array.from(document.querySelectorAll("[data-event-status]"));
  const empty = document.getElementById("event-filter-empty");
  if (!search || !cards.length) return;

  let status = "";

  function apply() {
    const query = search.value.trim().toLowerCase();
    let shown = 0;
    cards.forEach(function (card) {
      const visible = (!status || card.dataset.status === status) &&
        (!query || (card.dataset.name || "").includes(query));
      card.hidden = !visible;
      if (visible) shown += 1;
    });
    if (empty) empty.hidden = shown > 0;
  }

  search.addEventListener("input", apply);
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      status = chip.dataset.eventStatus || "";
      chips.forEach(function (item) {
        const active = item === chip;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", active ? "true" : "false");
      });
      apply();
    });
  });
})();
