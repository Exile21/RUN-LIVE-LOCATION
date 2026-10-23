/* Shared UI behaviour: control-room tabs, motion polish, tab bar indicator. */
(function () {
  "use strict";

  const Motion = window.Motion;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const desktop = window.matchMedia("(min-width: 900px)");

  function animate(target, keyframes, options) {
    if (!Motion || !target) return null;
    if (reduceMotion.matches) return null;
    try {
      return Motion.animate(target, keyframes, options);
    } catch (error) {
      return null;
    }
  }

  function spring(extra) {
    return Object.assign({ type: "spring", stiffness: 420, damping: 34 }, extra || {});
  }

  const api = {};

  /* ------------------------------------------------------------- tabs -- */

  const shell = document.querySelector(".map-shell");
  const tabbar = document.querySelector(".tabbar");
  const indicator = document.querySelector(".tabbar__indicator");

  function moveIndicator(button, instant) {
    if (!indicator || !button || !tabbar) return;
    if (!tabbar.offsetWidth) return;
    const left = button.offsetLeft - tabbar.clientLeft;
    const width = button.offsetWidth;
    indicator.style.width = width + "px";
    if (instant || !Motion || reduceMotion.matches) {
      indicator.style.transform = "translateX(" + left + "px)";
      indicator.style.opacity = "1";
      return;
    }
    animate(indicator, { x: left, opacity: 1 }, spring());
  }

  function setTab(name, options) {
    if (!shell) return;
    const opts = options || {};
    const next = shell.querySelector('.tabbar__item[data-tab="' + name + '"]');
    if (!next) return;
    if (shell.dataset.tab === name && !opts.force) {
      moveIndicator(next);
      return;
    }
    shell.dataset.tab = name;
    shell.querySelectorAll(".tabbar__item").forEach(function (item) {
      const selected = item === next;
      item.setAttribute("aria-selected", selected ? "true" : "false");
      item.setAttribute("tabindex", selected ? "0" : "-1");
    });
    moveIndicator(next);
    const section = shell.querySelector('.panel-section[data-section="' + name + '"]');
    if (section && !desktop.matches) {
      animate(section, { opacity: [0, 1], y: [12, 0] }, { duration: 0.26, ease: [0.2, 0.7, 0.3, 1] });
    }
    document.dispatchEvent(new CustomEvent("runline:tab", { detail: { tab: name } }));
  }

  api.setTab = setTab;

  if (shell && tabbar) {
    tabbar.addEventListener("click", function (event) {
      const button = event.target.closest(".tabbar__item");
      if (button) setTab(button.dataset.tab);
    });
    tabbar.addEventListener("keydown", function (event) {
      const keys = { ArrowLeft: -1, ArrowRight: 1 };
      if (!(event.key in keys)) return;
      event.preventDefault();
      const items = Array.prototype.slice.call(tabbar.querySelectorAll(".tabbar__item"));
      const index = items.indexOf(document.activeElement);
      const next = items[(index + keys[event.key] + items.length) % items.length];
      next.focus();
      setTab(next.dataset.tab);
    });
    document.addEventListener("click", function (event) {
      const trigger = event.target.closest("[data-tab-target]");
      if (trigger) setTab(trigger.dataset.tabTarget);
    });
    window.addEventListener("resize", function () {
      const active = shell.querySelector('.tabbar__item[aria-selected="true"]');
      if (active) moveIndicator(active, true);
    });
    requestAnimationFrame(function () {
      const active = shell.querySelector('.tabbar__item[aria-selected="true"]');
      if (active) {
        moveIndicator(active, true);
        shell.querySelectorAll(".tabbar__item").forEach(function (item) {
          item.setAttribute("tabindex", item === active ? "0" : "-1");
        });
      }
    });
  }

  /* --------------------------------------------------------- entrance -- */

  api.reveal = function (target) {
    let items = [];
    if (Array.isArray(target)) items = target;
    else if (target && target.nodeType === 1) {
      items = Array.prototype.slice.call(target.querySelectorAll(".runner-row"));
    } else if (target) items = Array.prototype.slice.call(target);
    if (!items.length) return;
    animate(
      items,
      { opacity: [0, 1], y: [8, 0] },
      { duration: 0.32, delay: Motion && Motion.stagger ? Motion.stagger(0.035) : 0, ease: [0.2, 0.7, 0.3, 1] }
    );
  };

  api.pulse = function (element) {
    if (!element) return null;
    return animate(element, { scale: [1, 1.45, 1] }, { duration: 1.6, repeat: Infinity, ease: "easeInOut" });
  };

  api.flash = function (element) {
    if (!element) return;
    animate(element, { scale: [1, 1.12, 1] }, { duration: 0.35, ease: [0.2, 0.7, 0.3, 1] });
  };

  window.RunlineUI = api;
})();
