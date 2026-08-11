(function () {
  "use strict";

  if (!document.documentElement || !document.body) return;

  const reducedMotion =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // 所有动效都以“渐进增强”方式工作：JS 没跑或用户关闭动效时，页面保持原样。
  document.documentElement.classList.add("js-ui-polish");
  if (reducedMotion) return;

  const finePointer =
    window.matchMedia &&
    window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  /* ---------- 滚动渐入 ---------- */
  const REVEAL_SELECTOR = [
    ".card",
    ".landing-section",
    ".stat",
    ".word-result",
    ".browser-card",
    ".empty-state",
    ".demo-article",
    ".demo-words",
    ".page-heading",
    ".home-review",
    ".auth-card",
    ".ai-article-result",
    ".landing-search-result",
    ".speaking-needs-list",
  ].join(",");

  const revealObserver =
    "IntersectionObserver" in window
      ? new IntersectionObserver(
          (entries) => {
            entries.forEach((entry) => {
              if (!entry.isIntersecting) return;
              entry.target.classList.add("ui-reveal-in");
              revealObserver.unobserve(entry.target);
            });
          },
          { threshold: 0.12, rootMargin: "0px 0px -6% 0px" }
        )
      : null;

  function setRevealDelay(el) {
    const group = el.parentElement;
    if (!group || !group.matches(".demo-cards, .stat-grid, .word-results, .speaking-needs-list")) {
      return;
    }
    const index = Array.prototype.indexOf.call(group.children, el);
    if (index > 0) {
      el.style.setProperty(
        "--ui-reveal-delay",
        Math.min(index * 55, 300) + "ms"
      );
    }
  }

  function applyReveal(root) {
    const nodes = (root || document).querySelectorAll(REVEAL_SELECTOR);
    nodes.forEach((el) => {
      if (el.classList.contains("ui-reveal")) return;
      // 复习卡每次评分都会整体重建，不能加透明渐入动画，
      // 否则每次换卡都会先透明后渐入，观感是卡片闪动。
      if (el.closest("#real-review-cards")) return;
      el.classList.add("ui-reveal");
      setRevealDelay(el);
      if (revealObserver) {
        revealObserver.observe(el);
      } else {
        el.classList.add("ui-reveal-in");
      }
    });
  }

  /* ---------- 卡片 3D 悬浮（只在支持精细指针的设备上启用） ---------- */
  const TILT_SELECTOR = [
    ".demo-card",
    ".stat",
    ".word-result",
    ".auth-card",
    ".landing-search-result",
  ].join(",");

  function setupTilt(root) {
    if (!finePointer) return;
    const nodes = (root || document).querySelectorAll(TILT_SELECTOR);
    nodes.forEach((el) => {
      if (el.dataset.uiTiltReady) return;
      el.dataset.uiTiltReady = "1";
      el.classList.add("ui-tilt");

      let frame = null;
      el.addEventListener("pointerenter", () => {
        el.style.willChange = "transform";
      });
      el.addEventListener("pointermove", (event) => {
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        const px = (event.clientX - rect.left) / rect.width - 0.5;
        const py = (event.clientY - rect.top) / rect.height - 0.5;
        const strength = el.classList.contains("auth-card")
          ? 2.5
          : el.classList.contains("word-result")
            ? 2
            : 4.5;
        if (frame) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
          el.style.transform =
            "perspective(900px) rotateX(" +
            (-py * strength).toFixed(2) +
            "deg) rotateY(" +
            (px * strength).toFixed(2) +
            "deg) translateY(-2px)";
        });
      });
      el.addEventListener("pointerleave", () => {
        if (frame) cancelAnimationFrame(frame);
        el.style.transform = "";
        el.style.willChange = "";
      });
    });
  }

  /* ---------- 跟随鼠标的高光 ---------- */
  const SPOTLIGHT_SELECTOR = [
    ".card",
    ".word-result",
    ".auth-card",
    ".demo-card-face",
    ".landing-search-result",
  ].join(",");

  function setupSpotlight(root) {
    if (!finePointer) return;
    const nodes = (root || document).querySelectorAll(SPOTLIGHT_SELECTOR);
    nodes.forEach((el) => {
      if (el.dataset.uiSpotReady) return;
      el.dataset.uiSpotReady = "1";
      el.addEventListener("pointermove", (event) => {
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        el.style.setProperty("--ui-spot-x", event.clientX - rect.left + "px");
        el.style.setProperty("--ui-spot-y", event.clientY - rect.top + "px");
      });
    });
  }

  function scan(root) {
    applyReveal(root);
    setupSpotlight(root);
    setupTilt(root);
  }

  scan(document);
  if ("MutationObserver" in window) {
    const observer = new MutationObserver(() => scan(document.body));
    observer.observe(document.body, { childList: true, subtree: true });
  }
})();
