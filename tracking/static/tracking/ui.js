/* Tabs work without the optional animation library. */
(function () {
  "use strict";

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const desktop = window.matchMedia("(min-width: 900px)");
  const shell = document.querySelector(".map-shell");
  const tabbar = document.querySelector(".tabbar");

  function animate(target, properties) {
    if (!window.anime || !target || reduceMotion.matches) return;
    window.anime.animate(target, Object.assign({ duration: 180, ease: "out(3)" }, properties));
  }

  function setTab(name, options) {
    if (!shell || !tabbar) return;
    const opts = options || {};
    const items = Array.from(tabbar.querySelectorAll(".tabbar__item"));
    const next = items.find(function (item) { return item.dataset.tab === name; }) || (opts.restore && items[0]);
    if (!next) return;
    name = next.dataset.tab;
    const changed = shell.dataset.tab !== name;
    shell.dataset.tab = name;
    items.forEach(function (item) {
      const selected = item === next;
      item.setAttribute("aria-selected", selected ? "true" : "false");
      item.setAttribute("tabindex", selected ? "0" : "-1");
    });
    if (!opts.restore) {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", name);
      window.history.replaceState(null, "", url);
    }
    if (changed && !opts.restore && !desktop.matches) {
      const panel = document.getElementById(next.getAttribute("aria-controls"));
      animate(panel, { opacity: [0.7, 1], translateY: [4, 0] });
    }
    document.dispatchEvent(new CustomEvent("runline:tab", { detail: { tab: name } }));
  }

  window.RunlineUI = {
    setTab: setTab,
    flash: function (element) { animate(element, { opacity: [0.65, 1], duration: 120 }); },
  };

  const accountMenu = document.querySelector(".account-menu");
  if (accountMenu) {
    document.addEventListener("click", function (event) {
      if (accountMenu.open && !accountMenu.contains(event.target)) accountMenu.open = false;
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && accountMenu.open) {
        accountMenu.open = false;
        accountMenu.querySelector("summary").focus();
      }
    });
  }

  if (shell && tabbar) {
    tabbar.addEventListener("click", function (event) {
      const button = event.target.closest(".tabbar__item");
      if (button) setTab(button.dataset.tab);
    });
    tabbar.addEventListener("keydown", function (event) {
      const items = Array.from(tabbar.querySelectorAll(".tabbar__item"));
      const index = items.indexOf(document.activeElement);
      if (index < 0) return;
      let next;
      if (event.key === "Home") next = items[0];
      else if (event.key === "End") next = items[items.length - 1];
      else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        const step = event.key === "ArrowLeft" ? -1 : 1;
        next = items[(index + step + items.length) % items.length];
      } else return;
      event.preventDefault();
      next.focus();
      setTab(next.dataset.tab);
    });
    document.addEventListener("click", function (event) {
      const trigger = event.target.closest("[data-tab-target]");
      if (trigger) {
        setTab(trigger.dataset.tabTarget);
        if (!desktop.matches) tabbar.querySelector('[aria-selected="true"]').focus();
        else document.getElementById("runner-search").focus();
      }
    });
    desktop.addEventListener("change", function () {
      document.dispatchEvent(new CustomEvent("runline:tab", { detail: { tab: "map" } }));
    });
    window.addEventListener("popstate", function () {
      setTab(new URL(window.location.href).searchParams.get("tab") || "map", { restore: true });
    });
    setTab(new URL(window.location.href).searchParams.get("tab") || "map", { restore: true });
    document.documentElement.classList.add("js");
  }
})();
