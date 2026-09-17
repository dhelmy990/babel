import {requestJSON, RequestError} from "./site.js";

const form = document.querySelector("#publishing-form");
const stateNode = document.querySelector("#publishing-state");

if (form && stateNode) {
  const {createArticleEditor} = await import("./article-editor.js");
  const state = JSON.parse(stateNode.textContent);
  const status = document.querySelector("#publish-status");
  const imageFiles = document.querySelector("#image-files");
  const imagePaths = document.querySelector("#image-paths");
  const previewBody = document.querySelector("[data-preview-body]");
  const previewHeader = document.querySelector("[data-preview-header]");
  const previewSources = document.querySelector("[data-preview-sources]");
  const previewButton = document.querySelector("[data-preview]");
  const submitButton = form.querySelector("button[type=submit]");
  const submissionId = state.submissionId;
  let inFlight = false;
  let dirty = false;
  let importing = Promise.resolve();
  const editor = createArticleEditor(form.querySelector(".article-editor"), state, {
    onChange: () => { dirty = true; }, onMessage: say,
  });
  form.addEventListener("input", () => { dirty = true; });
  window.addEventListener("beforeunload", event => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pagehide", event => { if (!event.persisted) editor.destroy(); });

  function say(message) { status.textContent = message; }

  function setBusy(value) {
    inFlight = value;
    previewButton.disabled = value;
    submitButton.disabled = value;
    form.querySelectorAll("input").forEach(input => { input.disabled = value; });
    editor.setBusy(value);
  }

  function showImagePaths() {
    imagePaths.replaceChildren();
    [...imageFiles.files].forEach((file) => {
      const label = document.createElement("label");
      label.textContent = `Logical path for ${file.name}`;
      const input = document.createElement("input");
      input.name = "image_path";
      input.type = "text";
      input.required = true;
      input.value = `images/${file.name}`;
      label.append(input);
      imagePaths.append(label);
    });
    refreshImportedImages();
    dirty = true;
  }

  function refreshImportedImages() {
    const paths = [...imagePaths.querySelectorAll("input")];
    editor.setImportedImages([...imageFiles.files].map((file, index) => [paths[index].value, file]));
  }

  function payload() {
    // Explicitly collect values because inputs are disabled during requests.
    const data = new FormData();
    data.append("title", form.elements.title.value);
    data.append("color", form.elements.color.value);
    data.append("markdown", editor.getMarkdown());
    const paths = [...imagePaths.querySelectorAll("input")];
    [...imageFiles.files].forEach((file, index) => {
      data.append("images", file);
      data.append("image_path", paths[index].value);
    });
    editor.appendImages(data);
    if (state.id) {
      data.append("article_id", state.id);
      data.append("revision", String(state.revision));
    } else {
      data.append("submission_id", submissionId);
    }
    return data;
  }

  function renderPreview(result) {
    previewBody.innerHTML = result.html;
    previewHeader.hidden = false;
    previewHeader.querySelector(".article-dot").style.background = result.color;
    previewHeader.querySelector(".article-date").textContent = "Preview";
    previewHeader.querySelector("h1").textContent = result.title;
    const destination = previewSources.querySelector("div");
    destination.replaceChildren();
    for (const [label, url] of result.sources || []) {
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = label;
      destination.append(link);
    }
    previewSources.hidden = !destination.children.length;
  }

  async function preview() {
    if (inFlight) return;
    setBusy(true);
    say("Preparing preview…");
    try {
      await importing;
      renderPreview(await requestJSON("/api/articles/preview", {method: "POST", body: payload()}));
      say("Preview ready.");
    } catch (error) {
      say(`Preview could not be prepared: ${error.message}`);
    } finally {
      setBusy(false);
    }
  }

  imageFiles.addEventListener("change", showImagePaths);
  imagePaths.addEventListener("input", refreshImportedImages);
  function importMarkdown() {
    const file = form.elements.markdown_file.files[0];
    if (!file) return;
    setBusy(true);
    say("Importing Markdown…");
    importing = (async () => {
      if (file.size > 2 * 1024 * 1024) throw new Error("Markdown must be 2 MB or smaller.");
      const text = new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
      editor.setMarkdown(text);
      dirty = true;
      say("Markdown imported. You can edit it above.");
    })();
    importing.catch(error => say(`Could not import Markdown: ${error.message}`)).finally(() => {
      importing = Promise.resolve();
      setBusy(false);
    });
  }
  form.elements.markdown_file.addEventListener("change", importMarkdown);
  previewButton.addEventListener("click", preview);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (inFlight) return;
    setBusy(true);
    say(state.id ? "Saving…" : "Publishing…");
    try {
      await importing;
      const endpoint = state.id ? `/api/articles/${state.id}` : "/api/articles";
      const result = await requestJSON(endpoint, {method: "POST", body: payload()});
      say("Saved. Opening article…");
      dirty = false;
      window.location.assign(`/${result.slug}`);
    } catch (error) {
      if (error instanceof RequestError && error.status === 409 && error.code === "revision_conflict") {
        say("This article changed elsewhere. Your edits and selected files are still here; reload before saving again.");
      } else {
        say(`The article could not be saved: ${error.message}`);
      }
      setBusy(false);
    }
  });
  form.querySelector("[data-publishing-fields]").disabled = false;
  // File selections can be restored or supplied while the editor module loads.
  // Reconcile their current values after registering the change handlers.
  if (imageFiles.files.length) showImagePaths();
  if (form.elements.markdown_file.files.length) importMarkdown();
}

document.querySelectorAll("[data-archive-article]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    const message = button.parentElement.querySelector("[role=status]");
    message.textContent = "Archiving…";
    try {
      await requestJSON(`/api/articles/${button.dataset.archiveArticle}/archive`, {method: "POST"});
      message.textContent = "Archived. Returning to timeline…";
      window.location.assign("/");
    } catch (error) {
      message.textContent = `Could not archive: ${error.message}`;
      button.disabled = false;
    }
  });
});
