/** A native scroll reveal; scheduling remains owned by reviews.js. */
export function createReadingReveal({marker, onComplete}) {
  const article = marker.parentElement.querySelector(".article-text");
  const pull = marker.querySelector("[data-reading-pull]");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let enabled = false, intended = false, completed = false, progress = 0, frame = 0, touchY = null;

  function measure() {
    const top = marker.getBoundingClientRect().top;
    const start = innerHeight - 90, finish = innerHeight * 0.15;
    return Math.max(0, Math.min(1, (start - top) / Math.max(1, start - finish)));
  }

  function update() {
    frame = 0;
    if (!enabled) return;
    progress = measure();
    marker.style.setProperty("--reveal-progress", String(progress));
    article.style.opacity = String(1 - progress * 0.8);
    if (progress === 1 && intended && !completed) onComplete();
  }
  function queue() { if (!frame) frame = requestAnimationFrame(update); }
  function editing(target) { return target instanceof Element && Boolean(target.closest("input, textarea, select, [contenteditable=true], .notes-panel")); }
  function intend(event) {
    if (!enabled || !event.isTrusted || editing(event.target)) return;
    // Restoration/resize cannot complete a review. Fresh downward input starts
    // the gesture only while there is still part of the reveal left to travel.
    if (measure() < 1) intended = true;
  }
  window.addEventListener("wheel", event => { if (event.deltaY > 0) intend(event); }, {passive: true});
  window.addEventListener("keydown", event => {
    if (["ArrowDown", "PageDown", "End", " "].includes(event.key) && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) intend(event);
  });
  window.addEventListener("touchstart", event => { touchY = event.touches[0]?.clientY ?? null; }, {passive: true});
  window.addEventListener("touchmove", event => {
    const y = event.touches[0]?.clientY;
    if (touchY !== null && y < touchY) intend(event);
    touchY = y ?? null;
  }, {passive: true});
  window.addEventListener("pointerdown", event => {
    if (event.clientX >= document.documentElement.clientWidth) intend(event);
  });
  window.addEventListener("scroll", queue, {passive: true});
  window.addEventListener("resize", () => {
    // A larger viewport can move a partial reveal past its threshold without
    // any further reading gesture. Require new input after layout changes.
    intended = false;
    queue();
  });
  pull.addEventListener("click", () => {
    if (!enabled) return;
    intended = true;
    const top = marker.getBoundingClientRect().top + scrollY - innerHeight * 0.15 + 2;
    window.scrollTo({top, behavior: reducedMotion.matches ? "instant" : "smooth"});
    queue();
  });

  return {
    ready: () => enabled && intended && measure() === 1,
    enable() { enabled = true; pull.disabled = false; update(); },
    confirm() { completed = true; marker.classList.add("is-complete"); },
    reset() {
      enabled = intended = completed = false;
      progress = 0;
      touchY = null;
      cancelAnimationFrame(frame);
      frame = 0;
      pull.disabled = true;
      marker.classList.remove("is-complete");
      marker.style.setProperty("--reveal-progress", "0");
      article.style.opacity = "";
    },
  };
}
