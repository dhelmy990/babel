import {requestJSON} from "./site.js";

const content = ({text, x, y}) => ({text, x, y});
const same = (a, b) => a && b && a.text === b.text && a.x === b.x && a.y === b.y;

/** One session's in-memory notes. Each note has its own serialized write queue. */
export function createNotesStore({articleId, signal, changed, removed, unauthorized}) {
  const entries = new Map();
  const endpoint = `/api/articles/${articleId}/notes`;
  let disposed = false;
  const request = (url, options = {}) => requestJSON(url, {...options, signal, cache: "no-store"});
  const notify = (entry) => { if (!disposed) changed(entry); };

  function add(note, persisted = true) {
    const entry = {
      id: note.id, kind: note.kind, confirmed: persisted ? {...note} : null,
      draft: content(note), queued: null, creation: null, busy: false,
      deleting: false, error: "", conflict: null, refreshConflict: false,
      status: persisted ? "Saved" : "Not saved", editing: !persisted,
    };
    entries.set(entry.id, entry);
    return entry;
  }

  function forget(entry) {
    entries.delete(entry.id);
    if (!disposed) removed(entry);
    entry.draft.text = "";
    if (entry.confirmed) entry.confirmed.text = "";
    if (entry.creation) entry.creation.text = "";
    if (entry.queued) entry.queued.text = "";
    if (entry.conflict) entry.conflict.text = "";
    entry.queued = entry.creation = entry.conflict = entry.confirmed = null;
  }

  async function refreshConflict(entry) {
    const result = await request(endpoint);
    if (disposed) return;
    const server = result.notes.find((note) => note.id === entry.id);
    if (!server) {
      entry.status = "This note is no longer available. Your unsaved text is still here.";
      entry.refreshConflict = false;
      entry.error = "missing";
      return;
    }
    entry.confirmed = server;
    entry.creation = null;
    entry.conflict = {...server};
    entry.refreshConflict = false;
    entry.status = "This note changed elsewhere. Compare the server copy, then Retry to save your text.";
  }

  async function pump(entry) {
    if (disposed || entry.busy || entry.error || (!entry.queued && !entry.deleting)) return;
    entry.busy = true;
    entry.status = "Saving…";
    notify(entry);
    try {
      while (!disposed && (entry.queued || entry.deleting)) {
        if (entry.deleting && !entry.creation) {
          if (entry.confirmed) {
            try { await request(`/api/notes/${entry.id}`, {method: "DELETE"}); }
            catch (error) { if (error.status !== 404) throw error; }
          }
          if (!disposed) forget(entry);
          return;
        }
        const attempted = entry.queued || content(entry.draft);
        entry.queued = null;
        let result;
        if (!entry.confirmed) {
          // A lost response must retry this exact original body, even after typing.
          entry.creation ||= {id: entry.id, kind: entry.kind, ...attempted};
          result = await request(endpoint, {method: "POST", body: entry.creation});
        } else {
          result = await request(`/api/notes/${entry.id}`, {
            method: "PATCH", body: {version: entry.confirmed.version, ...attempted},
          });
        }
        if (disposed) return;
        entry.confirmed = result.note;
        entry.creation = null;
        // Creation retry may confirm an older body; the queued desired edit follows.
        if (!same(attempted, entry.confirmed) && !entry.queued) entry.queued = attempted;
        entry.status = entry.queued || entry.deleting ? "Saving…" : (same(entry.draft, entry.confirmed) ? "Saved" : "Not saved");
        if (entry.status === "Saved") entry.editing = false;
        notify(entry);
      }
    } catch (error) {
      if (disposed) return;
      entry.queued ||= content(entry.draft);
      if (error.status === 401) {
        unauthorized();
        return;
      }
      entry.error = "retry";
      if (error.status === 409) {
        entry.refreshConflict = true;
        entry.status = "This note changed elsewhere. Loading the server copy…";
        notify(entry);
        try { await refreshConflict(entry); }
        catch (_) { entry.status = "The server copy could not be loaded. Your text is still here; Retry to compare."; }
      } else {
        entry.status = `The note could not be saved. Your text is still here. ${error.message}`;
      }
    } finally {
      entry.busy = false;
      notify(entry);
    }
  }

  function save(entry) {
    entry.queued = content(entry.draft);
    void pump(entry);
  }

  async function retry(entry) {
    if (entry.busy || disposed || entry.error === "missing") return;
    if (entry.refreshConflict) {
      entry.busy = true;
      try { await refreshConflict(entry); }
      catch (_) { if (!disposed) entry.status = "The server copy could not be loaded. Your text is still here; Retry to compare."; }
      finally { entry.busy = false; notify(entry); }
      return; // The reader must see the server copy before choosing to overwrite it.
    }
    entry.error = "";
    entry.conflict = null;
    save(entry);
  }

  function remove(entry) {
    // Deletion is a new explicit intent, independent of a paused failed edit.
    entry.error = "";
    entry.conflict = null;
    entry.refreshConflict = false;
    entry.deleting = true;
    void pump(entry);
  }

  function dispose() {
    disposed = true;
    for (const entry of entries.values()) forget(entry);
    entries.clear();
  }

  return {entries, add, save, retry, remove, dispose};
}
