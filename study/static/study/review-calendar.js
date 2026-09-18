import {requestJSON} from "./site.js";

const root = document.querySelector("[data-study-calendar]");
if (root) {
  const find = name => root.querySelector(`[data-calendar-${name}]`);
  const content = find("content"), status = find("status"), retry = find("retry");
  let generation = 0, controller = null, data = null, month = null, selected = null;

  // Construct UTC dates explicitly: date-only schedules must not shift with
  // browser offsets, and Date.UTC treats years 0–99 as 1900–1999.
  function date(value) {
    const [year, number, day] = value.split("-").map(Number);
    const result = new Date(0);
    result.setUTCFullYear(year, number - 1, day);
    result.setUTCHours(12, 0, 0, 0);
    return result;
  }
  const format = (value, options) => date(value).toLocaleDateString("en-GB", {...options, timeZone: "UTC"});

  function clear() {
    generation += 1;
    controller?.abort();
    controller = null;
    data = null;
    content.hidden = true;
    find("days").replaceChildren();
    find("items").replaceChildren();
    find("overdue").replaceChildren();
    retry.hidden = true;
    status.textContent = "";
  }

  function showItems(target, rows) {
    target.replaceChildren();
    for (const item of rows) {
      const li = document.createElement("li"), link = document.createElement("a");
      const dot = document.createElement("span"), title = document.createElement("span"), when = document.createElement("time");
      link.href = `/${item.slug}`;
      dot.className = "calendar-dot";
      dot.style.backgroundColor = item.color;
      dot.setAttribute("aria-hidden", "true");
      title.textContent = item.title;
      when.dateTime = item.due_date;
      when.textContent = format(item.due_date, {day: "numeric", month: "short"});
      link.append(dot, title, when);
      li.append(link);
      target.append(li);
    }
  }

  function selectDay(value) {
    selected = value;
    for (const button of find("days").querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.date === value));
    }
    find("selected-title").textContent = format(value, {weekday: "long", day: "numeric", month: "long"});
    const items = data.items.filter(item => item.due_date === value);
    showItems(find("items"), items);
    find("empty").hidden = Boolean(items.length);
  }

  function render() {
    month = data.month;
    find("month").textContent = format(`${month}-01`, {month: "long", year: "numeric"});
    find("timezone").textContent = `Dates in ${data.timezone}`;
    find("previous").disabled = month === "0001-01";
    find("next").disabled = month === "9999-12";
    const days = find("days");
    days.replaceChildren();
    const first = date(`${month}-01`);
    for (let index = 0; index < (first.getUTCDay() + 6) % 7; index++) days.append(document.createElement("span"));
    const last = new Date(first);
    last.setUTCMonth(last.getUTCMonth() + 1, 0);
    const counts = new Map();
    for (const item of data.items) counts.set(item.due_date, (counts.get(item.due_date) || 0) + 1);
    for (let day = 1; day <= last.getUTCDate(); day++) {
      const value = `${month}-${String(day).padStart(2, "0")}`, count = counts.get(value) || 0;
      const button = document.createElement("button"), number = document.createElement("span");
      button.type = "button";
      button.dataset.date = value;
      button.setAttribute("aria-label", `${format(value, {day: "numeric", month: "long", year: "numeric"})}, ${count} scheduled ${count === 1 ? "read" : "reads"}`);
      if (value === data.today) button.setAttribute("aria-current", "date");
      number.textContent = day;
      button.append(number);
      if (count) {
        const badge = document.createElement("span");
        badge.className = "calendar-count";
        badge.textContent = `${count} ${count === 1 ? "read" : "reads"}`;
        button.append(badge);
      }
      button.addEventListener("click", () => selectDay(value));
      days.append(button);
    }
    selectDay(selected?.startsWith(month + "-") ? selected : data.today.startsWith(month + "-") ? data.today : `${month}-01`);
    showItems(find("overdue"), data.overdue);
    find("overdue-count").textContent = data.overdue.length ? `· ${data.overdue.length}` : "";
    find("overdue-empty").hidden = Boolean(data.overdue.length);
    content.hidden = false;
  }

  async function load(requestedMonth = month) {
    clear();
    const current = generation;
    controller = new AbortController();
    const signal = controller.signal;
    month = requestedMonth;
    status.textContent = "Loading your schedule…";
    try {
      // Use the stored scheduling timezone. Synchronizing browser timezone here
      // can resolve an expired review day, so a calendar visit must not do it.
      const result = await requestJSON(`/api/reviews/calendar${month ? `?month=${encodeURIComponent(month)}` : ""}`, {signal, cache: "no-store"});
      if (current !== generation) return;
      data = result;
      render();
      status.textContent = "";
    } catch (error) {
      if (current !== generation) return;
      if (error.status === 401) { status.textContent = "Sign in to see your study calendar."; return; }
      status.textContent = `Your calendar could not be loaded. ${error.message}`;
      retry.hidden = false;
    }
  }

  function moveMonth(delta) {
    if (!data) return;
    const next = date(`${data.month}-01`);
    next.setUTCMonth(next.getUTCMonth() + delta);
    if (next.getUTCFullYear() < 1 || next.getUTCFullYear() > 9999) return;
    void load(`${String(next.getUTCFullYear()).padStart(4, "0")}-${String(next.getUTCMonth() + 1).padStart(2, "0")}`);
  }
  find("previous").addEventListener("click", () => moveMonth(-1));
  find("next").addEventListener("click", () => moveMonth(1));
  find("today").addEventListener("click", () => { selected = null; void load(null); });
  retry.addEventListener("click", () => void load());
  window.addEventListener("pagehide", clear);
  window.addEventListener("pageshow", event => { if (event.persisted) { selected = null; void load(null); } });
  window.addEventListener("review-auth-lost", clear);
  window.addEventListener("review-updated", () => void load());
  void load();
}
