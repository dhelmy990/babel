const outline = document.querySelector("[data-article-outline]");
const headings = [...document.querySelectorAll(".article-text .article-body h1, .article-text .article-body h2, .article-text .article-body h3")];

if (outline && headings.length) {
  const shell = outline.closest(".article-shell");
  const navigation = outline.querySelector("nav");
  const list = navigation.querySelector("ol");
  const toggle = outline.querySelector("[data-outline-toggle]");
  const smallScreen = matchMedia("(max-width: 1100px)");
  const usedIDs = new Set([...document.querySelectorAll("[id]")].map(element => element.id));
  const links = headings.map(heading => {
    if (!heading.id) {
      const slug = heading.textContent.normalize("NFKD").toLowerCase().replace(/\p{M}/gu, "").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "heading";
      const base = `section-${slug}`;
      let id = base;
      for (let suffix = 2; usedIDs.has(id); suffix++) id = `${base}-${suffix}`;
      heading.id = id;
      usedIDs.add(id);
    }
    heading.tabIndex = -1;
    const item = document.createElement("li");
    item.dataset.level = heading.tagName.slice(1);
    const link = document.createElement("a");
    link.href = `#${encodeURIComponent(heading.id)}`;
    link.textContent = heading.textContent;
    item.append(link);
    list.append(item);
    return link;
  });

  let compact = false;
  let current = -1;
  let frame = 0;
  function setExpanded(expanded) {
    toggle.setAttribute("aria-expanded", String(expanded));
    navigation.hidden = !expanded;
  }
  function updateLayout() {
    const next = smallScreen.matches || shell.classList.contains("notes-open");
    if (next !== compact) {
      compact = next;
      setExpanded(!compact);
    }
    shell.classList.toggle("compact-outline", compact);
  }
  function setCurrent(index) {
    if (index === current) return;
    if (current >= 0) links[current].removeAttribute("aria-current");
    current = index;
    links[index].setAttribute("aria-current", "location");
  }
  function updateCurrent() {
    frame = 0;
    const threshold = compact ? Math.max(100, outline.getBoundingClientRect().height + 32) : 80;
    let index = 0;
    for (let i = 0; i < headings.length; i++) {
      if (headings[i].getBoundingClientRect().top <= threshold) index = i;
      else break;
    }
    setCurrent(index);
  }
  function scheduleCurrent() {
    if (!frame) frame = requestAnimationFrame(updateCurrent);
  }
  function followHash() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    const index = headings.findIndex(heading => heading.id === id);
    if (index < 0) return;
    if (compact) setExpanded(false);
    headings[index].scrollIntoView({block: "start"});
    headings[index].focus({preventScroll: true});
    setCurrent(index);
  }

  toggle.addEventListener("click", () => setExpanded(toggle.getAttribute("aria-expanded") !== "true"));
  links.forEach((link, index) => link.addEventListener("click", event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
    if (compact) setExpanded(false);
    // Repeated activation of the same hash still jumps back to the heading.
    requestAnimationFrame(() => {
      headings[index].scrollIntoView({block: "start"});
      headings[index].focus({preventScroll: true});
      setCurrent(index);
    });
  }));
  shell.classList.add("has-article-outline");
  outline.hidden = false;
  setExpanded(true);
  updateLayout();
  updateCurrent();
  followHash();
  window.addEventListener("hashchange", followHash);
  window.addEventListener("scroll", scheduleCurrent, {passive: true});
  window.addEventListener("resize", scheduleCurrent);
  smallScreen.addEventListener("change", updateLayout);
  new MutationObserver(updateLayout).observe(shell, {attributes: true, attributeFilter: ["class"]});
}
