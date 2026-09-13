import {requestJSON, sessionReady} from "./site.js";

/** Start before eager images finish; restored pages always get a fresh lifecycle. */
function lifecycle(load, clear) {
  let started = false;
  const start = () => { started = true; void load(); };
  window.addEventListener("pagehide", () => { started = false; clear(); });
  window.addEventListener("pageshow", (event) => { if (event.persisted || !started) start(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
}

function imageSettled(image, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      image.removeEventListener("load", done);
      image.removeEventListener("error", done);
      signal.removeEventListener("abort", cancel);
    };
    const done = () => { cleanup(); resolve(); };
    const cancel = () => { cleanup(); reject(new DOMException("Page changed", "AbortError")); };
    image.addEventListener("load", done);
    image.addEventListener("error", done);
    signal.addEventListener("abort", cancel, {once: true});
    if (signal.aborted) cancel();
    else if (image.complete) done(); // Broken images also settle; they cannot block reading.
  });
}

async function articleLayoutReady(marker, signal) {
  const images = marker.closest(".annotation-surface").querySelectorAll(".article-body img");
  await Promise.all([...images].map((image) => imageSettled(image, signal)));
  await new Promise((resolve, reject) => {
    const cancel = () => { cancelAnimationFrame(frame); reject(new DOMException("Page changed", "AbortError")); };
    const frame = requestAnimationFrame(() => { signal.removeEventListener("abort", cancel); resolve(); });
    signal.addEventListener("abort", cancel, {once: true});
    if (signal.aborted) cancel();
  });
}

function endIsVisible(marker) {
  const box = marker.getBoundingClientRect();
  return document.visibilityState === "visible" && box.width > 0 && box.height > 0
    && box.top < innerHeight && box.bottom > 0 && box.left < innerWidth && box.right > 0;
}

const messages = {
  first_read: "Added to your study reviews",
  reviewed: "Review recorded",
  already_processed: "This reading was already recorded.",
  not_due: "This article is not due for review yet.",
  not_selected: "This article is not in today's review selection.",
};

export function mountReadingCompletion({articleId, marker}) {
  const controls = marker.querySelector("[data-reading-controls]");
  const status = controls.querySelector("[data-reading-status]");
  const retry = controls.querySelector("[data-reading-retry]");
  let generation = 0;
  let controller = null;
  let context = null;
  let observer = null;
  let busy = false;
  let acknowledged = false;
  let blocked = false;
  let layoutReady = false;

  function clear() {
    generation += 1;
    controller?.abort();
    controller = null;
    observer?.disconnect();
    observer = null;
    document.removeEventListener("visibilitychange", reached);
    context = null;
    busy = acknowledged = blocked = layoutReady = false;
    status.textContent = "";
    retry.hidden = controls.hidden = true;
    retry.disabled = false;
  }

  function fail(error, current) {
    if (current !== generation) return;
    if (error.status === 401) {
      window.dispatchEvent(new Event("review-auth-lost"));
      return;
    }
    blocked = true;
    status.textContent = `Reading completion could not be recorded. ${error.message}`;
    retry.hidden = false;
  }

  async function fetchContext(signal) {
    return requestJSON(`/api/articles/${articleId}/reading-context`, {signal, cache: "no-store"});
  }

  function stopObserving() {
    observer?.disconnect();
    document.removeEventListener("visibilitychange", reached);
  }

  function reached() {
    if (!blocked) void record();
  }

  async function record(allowExpiryRefresh = true) {
    if (busy || acknowledged || !context?.eligible || !layoutReady || !endIsVisible(marker)) return;
    const current = generation;
    const signal = controller.signal;
    busy = true;
    retry.disabled = true;
    blocked = false;
    retry.hidden = true;
    status.textContent = "Recording reading…";
    let retryFreshContext = false;
    try {
      const result = await requestJSON(`/api/articles/${articleId}/complete`, {
        method: "POST", body: {token: context.token}, signal, cache: "no-store",
      });
      if (current !== generation) return;
      acknowledged = true;
      status.textContent = messages[result.status] || "No review was recorded.";
      stopObserving();
      window.dispatchEvent(new Event("review-updated"));
    } catch (error) {
      if (current !== generation) return;
      if (error.status === 400 && error.code === "token_expired" && allowExpiryRefresh) {
        try {
          const refreshed = await fetchContext(signal);
          if (current !== generation) return;
          context = refreshed;
          if (context.eligible) retryFreshContext = true;
          else {
            acknowledged = true;
            status.textContent = messages[context.status] || "No review is due.";
            stopObserving();
            window.dispatchEvent(new Event("review-updated"));
          }
        } catch (refreshError) { fail(refreshError, current); }
      } else fail(error, current);
    } finally {
      if (current === generation) {
        busy = false;
        retry.disabled = false;
        if (retryFreshContext) void record(false); // Bound automatic expiry recovery.
      }
    }
  }

  async function load() {
    clear();
    const current = generation;
    controller = new AbortController();
    const signal = controller.signal;
    controls.hidden = false;
    status.textContent = "Preparing study reviews…";
    try {
      const session = await sessionReady();
      if (current !== generation) return;
      if (!session.authenticated) { clear(); return; }
      const result = await fetchContext(signal);
      if (current !== generation) return;
      context = result;
      if (!context.eligible) {
        acknowledged = true;
        status.textContent = messages[context.status] || "No review is due.";
        return;
      }
      await articleLayoutReady(marker, signal);
      if (current !== generation) return;
      layoutReady = true;
      status.textContent = "";
      observer = new IntersectionObserver(reached);
      observer.observe(marker);
      document.addEventListener("visibilitychange", reached);
      reached(); // Short articles legitimately start with their end in view.
    } catch (error) { fail(error, current); }
  }

  retry.addEventListener("click", () => {
    if (busy) return;
    if (context && layoutReady) void record();
    else void load();
  });
  window.addEventListener("review-auth-lost", clear);
  lifecycle(load, clear);
}

/** One today response supplies both the optional home list and the navbar count. */
export function mountReviewList(container) {
  const navigation = document.querySelector("[data-review-navigation]");
  const link = navigation.querySelector("[data-review-link]");
  const retry = navigation.querySelector("[data-review-retry]");
  const navStatus = navigation.querySelector("[data-review-nav-status]");
  const list = container.matches("[data-review-list]") ? container : null;
  const items = list?.querySelector("[data-review-items]");
  const status = list?.querySelector("[data-review-status]");
  const count = list?.querySelector("[data-review-count]");
  const day = list?.querySelector("[data-review-day]");
  let generation = 0;
  let controller = null;

  function clear() {
    generation += 1;
    controller?.abort();
    controller = null;
    navigation.hidden = true;
    link.textContent = "Review today";
    retry.hidden = navStatus.hidden = true;
    navStatus.textContent = "";
    items?.replaceChildren();
    if (list) {
      list.hidden = true;
      count.textContent = day.textContent = status.textContent = "";
    }
  }

  function openFromHash() {
    if (list && !list.hidden && location.hash === "#reviews") {
      list.open = true;
      list.scrollIntoView({block: "start"});
    }
  }

  async function load() {
    clear();
    const current = generation;
    controller = new AbortController();
    const signal = controller.signal;
    if (status) status.textContent = "Loading reviews…";
    try {
      const session = await sessionReady();
      if (current !== generation) return;
      if (!session.authenticated) { clear(); return; }
      navigation.hidden = false;
      if (list) list.hidden = false;
      const result = await requestJSON("/api/reviews/today", {method: "POST", signal, cache: "no-store"});
      if (current !== generation) return;
      const outstanding = result.slots.filter((slot) => !slot.completed && !slot.cancelled);
      link.textContent = `Review today · ${outstanding.length}`;
      if (list) {
        count.textContent = `${outstanding.length} remaining`;
        day.textContent = `${result.date} · ${result.timezone}`;
        status.textContent = outstanding.length ? "" : "Nothing to review today.";
        for (const slot of outstanding) {
          const anchor = document.createElement("a");
          anchor.href = `/${slot.slug}`;
          anchor.textContent = slot.title;
          items.append(anchor);
        }
        openFromHash();
      }
    } catch (error) {
      if (current !== generation) return;
      if (error.status === 401) { window.dispatchEvent(new Event("review-auth-lost")); return; }
      navigation.hidden = false;
      retry.hidden = navStatus.hidden = false;
      navStatus.textContent = "Reviews could not be loaded.";
      if (list) {
        list.hidden = false;
        status.textContent = "Reviews could not be loaded. Use Retry reviews to try again.";
        openFromHash();
      }
    } finally {
      if (current === generation) retry.disabled = false;
    }
  }

  retry.addEventListener("click", () => { retry.disabled = true; void load(); });
  link.addEventListener("click", (event) => {
    if (list && !list.hidden && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
      list.open = true; // Also reopen when the URL already has #reviews.
    }
  });
  window.addEventListener("hashchange", openFromHash);
  window.addEventListener("review-updated", load);
  window.addEventListener("review-auth-lost", clear);
  lifecycle(load, clear);
}

const navigation = document.querySelector("[data-review-navigation]");
const activePage = document.querySelector(".home, .galaxy-shell, [data-reading-article]");
if (navigation && activePage && !navigation.hasAttribute("data-review-passive")) {
  mountReviewList(document.querySelector("[data-review-list]") || navigation);
}
const marker = document.querySelector("[data-reading-article]");
if (marker) mountReadingCompletion({articleId: marker.dataset.readingArticle, marker});
