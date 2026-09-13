import {requestJSON} from "./site.js";
import {createNotesStore} from "./notes-store.js";

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function button(text, action, className = "") {
  const node = element("button", className, text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

/** Mount one article. Private content exists only during this page's active lifetime. */
export function mountNotes({articleId, surface, sidebar, toggle}) {
  const shell = surface.closest(".article-shell");
  const overlay = surface.querySelector("[data-notes-overlay]");
  const list = sidebar.querySelector("[data-notes-list]");
  const status = sidebar.querySelector("[data-notes-status]");
  const count = sidebar.querySelector("[data-notes-count]");
  const createControls = sidebar.querySelector("[data-notes-create]");
  const reload = sidebar.querySelector("[data-notes-reload]");
  const signIn = shell.querySelector("[data-notes-signin]");
  const mobile = window.matchMedia("(max-width: 850px)");
  const views = new Map();
  const mobilePlacements = new Set();
  let store = null;
  let controller = null;
  let generation = 0;
  let drag = null;

  function open(value, restoreFocus = false) {
    sidebar.hidden = !value;
    shell.classList.toggle("notes-open", value);
    toggle.setAttribute("aria-expanded", String(value));
    if (value) sidebar.querySelector("[data-notes-close]").focus({preventScroll: true});
    else if (restoreFocus && !toggle.hidden) toggle.focus({preventScroll: true});
    renderPositions();
  }

  function bounds(copy) {
    return {
      x: Math.max(0, Math.min(1000000, surface.clientWidth - copy.offsetWidth)),
      y: Math.max(0, Math.min(1000000, surface.clientHeight - copy.offsetHeight)),
    };
  }

  function clamp(position, copy) {
    const max = bounds(copy);
    return {x: Math.min(max.x, Math.max(0, position.x)), y: Math.min(max.y, Math.max(0, position.y))};
  }

  function renderPosition(entry, view) {
    const {x, y} = entry.draft;
    // Measure without display:none; visibility avoids changing surface geometry.
    view.copy.hidden = false;
    const max = bounds(view.copy);
    const outside = x === null || y === null || x > max.x || y > max.y;
    const mobileSidebarOnly = mobile.matches && !mobilePlacements.has(entry.id);
    view.copy.hidden = outside || mobileSidebarOnly;
    view.copy.style.left = `${x ?? 0}px`;
    view.copy.style.top = `${y ?? 0}px`;
    view.location.textContent = x === null ? "In sidebar" : outside ? "Outside the current article · Place here to recover"
      : mobileSidebarOnly ? "In sidebar on this screen" : "Placed on article";
  }

  function renderPositions() {
    for (const [id, view] of views) {
      const entry = store?.entries.get(id);
      if (entry) renderPosition(entry, view);
    }
  }

  function update(entry) {
    const view = views.get(entry.id);
    if (!view) return;
    view.text.textContent = entry.draft.text;
    view.copyText.textContent = entry.draft.text;
    view.editor.hidden = !entry.editing;
    view.text.hidden = entry.editing;
    // Only initialize values through edit/input; never overwrite active typing on ack.
    if (view.input.value !== entry.draft.text) view.input.value = entry.draft.text;
    view.status.textContent = entry.status;
    view.retry.hidden = !entry.error || entry.error === "missing";
    view.retry.disabled = entry.busy;
    view.save.disabled = Boolean(entry.error) || entry.deleting;
    view.server.hidden = !entry.conflict;
    view.server.textContent = entry.conflict ? `Current server copy:\n${entry.conflict.text}` : "";
    view.deleteButton.disabled = entry.deleting;
    renderPosition(entry, view);
  }

  function removeView(entry) {
    const view = views.get(entry.id);
    if (view) {
      view.input.value = "";
      view.card.replaceChildren();
      view.copy.replaceChildren();
      view.card.remove();
      view.copy.remove();
      views.delete(entry.id);
      mobilePlacements.delete(entry.id);
    }
    count.textContent = String(views.size);
  }

  function edit(entry) {
    entry.editing = true;
    open(true);
    update(entry);
    const input = views.get(entry.id).input;
    input.focus({preventScroll: true});
    input.scrollIntoView({block: "nearest"});
  }

  function place(entry) {
    const rect = surface.getBoundingClientRect();
    const view = views.get(entry.id);
    view.copy.hidden = false;
    Object.assign(entry.draft, clamp({x: 16, y: Math.max(16, -rect.top + 20)}, view.copy));
    mobilePlacements.add(entry.id);
    if (mobile.matches) open(false, true);
    update(entry);
    store.save(entry);
  }

  function cancelDrag() {
    if (!drag) return;
    const {entry, grip, pointerId} = drag;
    drag = null;
    // An earlier queued write may have confirmed while this drag was active.
    entry.draft.x = entry.confirmed?.x ?? null;
    entry.draft.y = entry.confirmed?.y ?? null;
    if (!entry.busy && !entry.error) {
      entry.status = entry.confirmed?.text === entry.draft.text ? "Saved" : "Not saved";
    }
    if (grip.hasPointerCapture(pointerId)) grip.releasePointerCapture(pointerId);
    update(entry);
  }

  function wireMovement(entry, copy, grip) {
    grip.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || drag) return;
      event.preventDefault();
      const rect = surface.getBoundingClientRect();
      drag = {entry, grip, pointerId: event.pointerId,
        offsetX: event.clientX - rect.left - entry.draft.x,
        offsetY: event.clientY - rect.top - entry.draft.y};
      grip.setPointerCapture(event.pointerId);
      grip.focus({preventScroll: true});
    });
    grip.addEventListener("pointermove", (event) => {
      if (!drag || drag.entry !== entry || drag.pointerId !== event.pointerId) return;
      const rect = surface.getBoundingClientRect();
      Object.assign(entry.draft, clamp({x: event.clientX - rect.left - drag.offsetX,
        y: event.clientY - rect.top - drag.offsetY}, copy));
      renderPosition(entry, views.get(entry.id));
    });
    grip.addEventListener("pointerup", (event) => {
      if (!drag || drag.entry !== entry || drag.pointerId !== event.pointerId) return;
      drag = null;
      if (grip.hasPointerCapture(event.pointerId)) grip.releasePointerCapture(event.pointerId);
      store.save(entry);
    });
    grip.addEventListener("pointercancel", cancelDrag);
    grip.addEventListener("lostpointercapture", cancelDrag);
    grip.addEventListener("keydown", (event) => {
      const directions = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
      if (!directions[event.key]) return;
      event.preventDefault();
      const [dx, dy] = directions[event.key];
      const step = event.shiftKey ? 1 : 10;
      Object.assign(entry.draft, clamp({x: entry.draft.x + dx * step, y: entry.draft.y + dy * step}, copy));
      update(entry);
      store.save(entry);
    });
  }

  function addView(entry) {
    const card = element("section", "note");
    card.dataset.noteId = entry.id;
    card.setAttribute("aria-label", entry.kind === "sticky" ? "Sticky note" : "Text box");
    const heading = element("h3", "note-kind", entry.kind === "sticky" ? "Sticky note" : "Text box");
    const text = element("p", "note-text");
    const location = element("small", "note-location");
    const editor = element("form", "note-editor");
    const label = element("label", "", "Note text");
    const input = element("textarea");
    input.maxLength = 20000;
    input.rows = 6;
    input.value = entry.draft.text;
    input.addEventListener("input", () => {
      entry.draft.text = input.value;
      if (!entry.busy && !entry.error) entry.status = "Not saved";
      update(entry);
    });
    label.append(input);
    const save = element("button", "", "Save note");
    save.type = "submit";
    editor.append(label, save);
    editor.addEventListener("submit", (event) => { event.preventDefault(); if (!entry.error) store.save(entry); });
    const actions = element("div", "note-actions");
    const deleteButton = button("Delete", () => store.remove(entry));
    actions.append(button("Edit", () => edit(entry)), button("Place here", () => place(entry)),
      button("Return to sidebar", () => {
        entry.draft.x = entry.draft.y = null;
        mobilePlacements.delete(entry.id);
        update(entry);
        store.save(entry);
      }), deleteButton);
    const message = element("p", "note-status");
    message.setAttribute("role", "status");
    message.setAttribute("aria-live", "polite");
    const server = element("p", "note-server");
    server.dataset.serverCopy = "";
    const retry = button("Retry", () => store.retry(entry));
    card.append(heading, text, location, editor, actions, message, server, retry);
    const copy = element("section", "note positioned-note");
    copy.dataset.noteCopy = entry.id;
    const grip = button("⠿", () => {}, "handle");
    grip.setAttribute("aria-label", "Move note");
    grip.title = "Drag, or move with arrow keys (Shift for 1 pixel)";
    const copyText = element("p", "note-text");
    copy.append(grip, copyText, button("Edit note", () => edit(entry)));
    wireMovement(entry, copy, grip);
    views.set(entry.id, {card, text, location, editor, input, save, status: message, server, retry, deleteButton, copy, copyText});
    list.append(card);
    overlay.append(copy);
    update(entry);
    count.textContent = String(views.size);
  }

  function clearPrivate() {
    generation += 1;
    controller?.abort();
    controller = null;
    drag = null;
    store?.dispose();
    store = null;
    for (const view of views.values()) {
      view.input.value = "";
      view.card.replaceChildren();
      view.copy.replaceChildren();
    }
    views.clear();
    mobilePlacements.clear();
    list.replaceChildren();
    overlay.replaceChildren();
    count.textContent = "0";
    status.textContent = "";
    createControls.hidden = true;
    reload.hidden = true;
    toggle.hidden = true;
    open(false);
  }

  async function load() {
    clearPrivate();
    const current = generation;
    controller = new AbortController();
    const signal = controller.signal;
    status.textContent = "Loading notes…";
    try {
      const session = await requestJSON("/api/session", {signal, cache: "no-store"});
      if (generation !== current) return;
      signIn.hidden = Boolean(session.authenticated);
      if (!session.authenticated) { status.textContent = ""; return; }
      toggle.hidden = false;
      const result = await requestJSON(`/api/articles/${articleId}/notes`, {signal, cache: "no-store"});
      if (generation !== current) return;
      store = createNotesStore({articleId, signal, changed: update, removed: removeView,
        unauthorized: () => { clearPrivate(); signIn.hidden = false; }});
      for (const note of result.notes) addView(store.add(note));
      status.textContent = "";
      createControls.hidden = false;
    } catch (_) {
      if (generation !== current) return;
      status.textContent = "Notes could not be loaded. Retry when the connection returns.";
      toggle.hidden = false;
      reload.hidden = false;
    }
  }

  toggle.addEventListener("click", () => open(sidebar.hidden));
  sidebar.querySelector("[data-notes-close]").addEventListener("click", () => open(false, true));
  reload.addEventListener("click", async () => { await load(); if (!toggle.hidden) open(true); });
  sidebar.querySelectorAll("[data-new-note]").forEach((control) => {
    control.addEventListener("click", () => {
      if (!store) return;
      const entry = store.add({id: crypto.randomUUID(), kind: control.dataset.newNote, text: "", x: null, y: null}, false);
      addView(entry);
      edit(entry);
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { cancelDrag(); if (!sidebar.hidden) open(false, true); }
  });
  // A restored page must never paint another reader's cached private DOM.
  window.addEventListener("pagehide", clearPrivate);
  window.addEventListener("pageshow", load);
  window.addEventListener("resize", renderPositions);
  new ResizeObserver(renderPositions).observe(surface);
  if (document.readyState === "complete") void load();
}

const article = document.querySelector("[data-notes-article]");
if (article) mountNotes({articleId: article.dataset.notesArticle,
  surface: article.querySelector("[data-annotation-surface]"),
  sidebar: article.querySelector("[data-notes-sidebar]"), toggle: article.querySelector("[data-notes-toggle]")});
