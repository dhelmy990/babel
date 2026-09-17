import {Editor, StarterKit, Image, TableKit, Markdown, TaskList, TaskItem, Link, Paragraph, marked} from "../vendor/article-editor.js";

// Match the server's html=False: angle-bracket text and comments are prose.
marked.use({tokenizer: {html() {}, tag() {}}});

const escapeMarkdown = text => String(text || "").replace(/[\\[\]"*_`~]/g, "\\$&");
const markdownDestination = value => String(value || "").replace(/</g, "%3C").replace(/>/g, "%3E").replace(/[\r\n]/g, "");
const inlineText = nodes => nodes.map(node => node.text ??
  (node.type === "hardBreak" ? " " : node.type === "image" ? node.attrs.alt : inlineText(node.content || []))).join("");

/** Markdown stays canonical; browser image URLs never enter a saved document. */
export function createArticleEditor(root, state, {onChange, onMessage}) {
  const visual = root.querySelector("[data-visual-editor]");
  const source = root.querySelector("[data-markdown-source]");
  const toolbar = root.querySelector(".editor-toolbar");
  const imageInput = root.querySelector("[data-inline-images]");
  const linkPanel = root.querySelector(".editor-link");
  const linkInput = root.querySelector("[data-link-url]");
  const pendingImages = new Map();
  const importedImages = new Map();
  const objectURLs = new Set();
  let markdown = state.markdown || "";
  let mode = "write";
  let busy = false;

  function imageURL(path) {
    let name = path;
    try { name = decodeURIComponent(path).replaceAll("\\", "/").replace(/^\.\//, ""); } catch { /* Invalid paths are rejected on save. */ }
    return pendingImages.get(name)?.url || importedImages.get(name)?.url || state.imageUrls?.[name];
  }

  const ArticleImage = Image.extend({
    parseMarkdown(token, helpers) {
      return helpers.createNode("image", {
        src: token.href, title: token.title,
        alt: inlineText(helpers.parseInline(token.tokens || [])),
      });
    },
    renderMarkdown(node) {
      // Angle-bracket destinations retain spaces and parentheses in uploaded
      // filenames; escape delimiters so the server sees the same logical path.
      const path = markdownDestination(node.attrs.src);
      const title = node.attrs.title ? ` "${escapeMarkdown(node.attrs.title)}"` : "";
      return `![${escapeMarkdown(node.attrs.alt).replaceAll("&", "&amp;")}](<${path}>${title})`;
    },
    renderHTML({HTMLAttributes}) {
      const {src, ...attributes} = HTMLAttributes;
      return ["img", {...attributes, "data-image-path": src, ...(imageURL(src) ? {src: imageURL(src)} : {})}];
    },
    addAttributes() {
      return {
        ...this.parent(),
        src: {
          default: null,
          parseHTML: element => element.getAttribute("data-image-path") ||
            Object.entries(state.imageUrls || {}).find(([, url]) => url === element.getAttribute("src"))?.[0] ||
            element.getAttribute("src"),
        },
      };
    },
  }).configure({inline: true});

  const ArticleLink = Link.extend({
    renderMarkdown(node, helpers) {
      const title = node.attrs.title ? ` "${escapeMarkdown(node.attrs.title)}"` : "";
      return `[${helpers.renderChildren(node)}](<${markdownDestination(node.attrs.href)}>${title})`;
    },
  }).configure({openOnClick: false});

  const ArticleParagraph = Paragraph.extend({
    parseMarkdown(token, helpers) {
      // The default parser unwraps lone images as block nodes. Our images are
      // inline, so even an image-only paragraph must retain its paragraph.
      // This wrapper also provides a place to position the text cursor.
      if (token.tokens?.length === 1 && token.tokens[0].type === "image") {
        return helpers.createNode("paragraph", undefined, helpers.parseInline(token.tokens));
      }
      return this.parent(token, helpers);
    },
  });

  const editor = new Editor({
    element: visual,
    extensions: [
      StarterKit.configure({underline: false, link: false, paragraph: false}),
      ArticleParagraph, ArticleImage, ArticleLink, TableKit, TaskList, TaskItem.configure({nested: true}), Markdown,
    ],
    content: markdown,
    contentType: "markdown",
    editorProps: {
      attributes: {role: "textbox", "aria-label": "Article body", "aria-multiline": "true", "data-placeholder": "Start writing…"},
      handlePaste(_view, event) {
        const files = [...(event.clipboardData?.files || [])];
        if (!files.length) return false;
        insertImages(files);
        return true;
      },
      handleDrop(view, event, _slice, moved) {
        const files = [...(event.dataTransfer?.files || [])];
        if (moved || !files.length) return false;
        const position = view.posAtCoords({left: event.clientX, top: event.clientY});
        if (position) editor.commands.setTextSelection(position.pos);
        insertImages(files);
        return true;
      },
    },
    onUpdate() {
      markdown = editor.getMarkdown();
      onChange();
    },
    onSelectionUpdate: updateToolbar,
    onTransaction: updateToolbar,
  });

  function focusedChain() {
    // Tiptap's focus command defers to requestAnimationFrame for React. This
    // vanilla editor must focus immediately so it cannot steal a later click.
    editor.view.focus();
    return editor.chain();
  }

  function updateToolbar() {
    // Tiptap may dispatch an initialization transaction before construction ends.
    if (!visual.querySelector(".tiptap")) return;
    queueMicrotask(() => {
      root.querySelectorAll("[data-editor-command]").forEach(button => {
        const command = button.dataset.editorCommand;
        if (command === "undo" || command === "redo") {
          button.disabled = busy || mode !== "write" || !editor.can()[command]();
        } else {
          button.setAttribute("aria-pressed", String(editor.isActive(command, command === "heading" ? {level: 2} : {})));
        }
      });
    });
  }

  function objectURL(file) {
    const url = URL.createObjectURL(file);
    objectURLs.add(url);
    return url;
  }

  function insertImages(files) {
    if (busy) return;
    const extensions = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"};
    if (files.some(file => !extensions[file.type])) {
      onMessage("Choose a PNG, JPEG, or WebP image.");
      return;
    }
    if (files.some(file => file.size > 10 * 1024 * 1024)) {
      onMessage("Each image must be 10 MB or smaller.");
      return;
    }
    const images = files.map(file => {
      const path = `images/${crypto.randomUUID()}.${extensions[file.type]}`;
      pendingImages.set(path, {file, url: objectURL(file)});
      return {type: "image", attrs: {src: path, alt: ""}};
    });
    if (mode === "markdown") {
      source.setRangeText(images.map(image => `![](${image.attrs.src})`).join("\n\n"), source.selectionStart, source.selectionEnd, "end");
      markdown = source.value;
      source.focus();
      onChange();
    } else {
      focusedChain().insertContent(images).run();
    }
    onMessage("Image added. It will be uploaded when you save.");
  }

  function setMarkdown(value) {
    editor.commands.setContent(value, {contentType: "markdown", emitUpdate: false});
    // Preserve exact source, including reference definitions and whitespace,
    // until the user makes a real visual edit.
    markdown = value;
    source.value = value;
  }

  function switchMode(next) {
    if (busy || next === mode) return;
    linkPanel.hidden = true;
    if (next === "write") setMarkdown(source.value);
    else source.value = markdown;
    mode = next;
    visual.hidden = mode !== "write";
    source.hidden = mode !== "markdown";
    toolbar.querySelectorAll("[data-editor-command]").forEach(button => { button.disabled = mode !== "write"; });
    root.querySelectorAll("[data-editor-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.editorMode === mode)));
    if (mode === "write") editor.view.focus();
    else source.focus();
    updateToolbar();
  }

  root.querySelectorAll("[data-editor-mode]").forEach(button => button.addEventListener("click", () => switchMode(button.dataset.editorMode)));
  source.addEventListener("input", () => { markdown = source.value; onChange(); });
  source.addEventListener("paste", event => {
    const files = [...(event.clipboardData?.files || [])];
    if (files.length) { event.preventDefault(); insertImages(files); }
  });
  toolbar.addEventListener("mousedown", event => {
    if (event.target.closest("button")) event.preventDefault();
  });
  const commands = {
    bold: () => focusedChain().toggleBold().run(),
    italic: () => focusedChain().toggleItalic().run(),
    heading: () => focusedChain().toggleHeading({level: 2}).run(),
    bulletList: () => focusedChain().toggleBulletList().run(),
    orderedList: () => focusedChain().toggleOrderedList().run(),
    blockquote: () => focusedChain().toggleBlockquote().run(),
    codeBlock: () => focusedChain().toggleCodeBlock().run(),
    undo: () => focusedChain().undo().run(),
    redo: () => focusedChain().redo().run(),
    link: () => {
      linkPanel.hidden = false;
      linkInput.value = editor.getAttributes("link").href || "";
      linkInput.focus();
    },
  };
  toolbar.addEventListener("click", event => {
    const command = event.target.closest("[data-editor-command]")?.dataset.editorCommand;
    if (command && !busy && mode === "write") commands[command]();
  });
  function applyLink() {
    const url = linkInput.value.trim();
    if (!/^(https?:\/\/|mailto:)/i.test(url)) { onMessage("Use an https://, http://, or mailto: link."); return; }
    focusedChain().extendMarkRange("link").setLink({href: url}).run();
    linkPanel.hidden = true;
  }
  root.querySelector("[data-apply-link]").addEventListener("click", applyLink);
  root.querySelector("[data-remove-link]").addEventListener("click", () => { focusedChain().extendMarkRange("link").unsetLink().run(); linkPanel.hidden = true; });
  root.querySelector("[data-cancel-link]").addEventListener("click", () => { linkPanel.hidden = true; editor.view.focus(); });
  linkInput.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); applyLink(); }
    if (event.key === "Escape") { event.preventDefault(); linkPanel.hidden = true; editor.view.focus(); }
  });
  root.querySelector("[data-insert-image]").addEventListener("click", () => imageInput.click());
  imageInput.addEventListener("change", () => { insertImages([...imageInput.files]); imageInput.value = ""; });

  return {
    getMarkdown: () => markdown,
    setMarkdown,
    appendImages(data) {
      for (const [path, {file}] of pendingImages) {
        // Removed images remain available to Undo, but are not uploaded.
        if (!markdown.includes(path)) continue;
        data.append("images", file, path.split("/").pop());
        data.append("image_path", path);
      }
    },
    setImportedImages(images) {
      for (const {url} of importedImages.values()) { URL.revokeObjectURL(url); objectURLs.delete(url); }
      importedImages.clear();
      for (const [path, file] of images) importedImages.set(path, {file, url: objectURL(file)});
      visual.querySelectorAll("img[data-image-path]").forEach(image => {
        const url = imageURL(image.dataset.imagePath);
        if (url) image.src = url;
        else image.removeAttribute("src");
      });
    },
    setBusy(value) {
      busy = value;
      editor.setEditable(!value, false);
      source.disabled = value;
      root.querySelectorAll("button").forEach(button => { button.disabled = value || (mode !== "write" && Boolean(button.dataset.editorCommand)); });
      updateToolbar();
    },
    destroy() {
      editor.destroy();
      objectURLs.forEach(url => URL.revokeObjectURL(url));
    },
  };
}
