// ============================================
// QUILL EDITOR
// ============================================

const Editor = {
    // Quill editor instance
    editor: null,

    // Auto-save timer handle
    autoSaveTimer: null,

    // Current heading level for cycling (0 = normal, 1-3 = h1-h3)
    currentHeadingLevel: 0,

    // Track if Quill blots have been registered
    blotsRegistered: false,

    /**
     * Initialize Quill editor with custom blots and keyboard shortcuts.
     * Safe to call multiple times — skips if already initialized.
     */
    initEditor() {
        if (this.editor) return;

        const Inline = Quill.import('blots/inline');
        const Embed = Quill.import('blots/embed');

        if (this.blotsRegistered) {
            this.createQuillInstance();
            return;
        }
        this.blotsRegistered = true;

        class HighlightBlot extends Inline {
            static create() {
                const node = super.create();
                node.style.backgroundColor = 'rgba(255, 230, 0, 0.3)';
                return node;
            }
        }
        HighlightBlot.blotName = 'highlight';
        HighlightBlot.tagName = 'mark';
        Quill.register(HighlightBlot);

        class LinkBlot extends Embed {
            static create(value) {
                const node = super.create();
                node.setAttribute('data-url', value.url);
                node.setAttribute('contenteditable', 'false');


                const domain = value.url.replace(/^(https?:\/\/)?(www\.)?/, '').split('/')[0];
                const faviconUrl = `https://www.google.com/s2/favicons?sz=32&domain=${domain}`;
                
                // Truncate the domain to use as a fallback title for now
                const displayTitle = "link";
                // Construct the internal HTML structure
                const iconHtml = `<img src="${faviconUrl}" alt="" style="width: 16px; height: 16px; vertical-align: middle; margin-right: 6px; border-radius: 2px;" />`;
                node.innerHTML = `${iconHtml}<span class="link-embed-label">${displayTitle}</span>`;
                node.onhover = () => {
                    node.style.borderColor = '#0076fcff';
                };
                node.onclick = () => {
                    setTimeout(() => { node.style.borderColor = ''; }, 500);
                    window.open(value.url, '_blank', 'noopener,noreferrer');
                };
                return node;
            }
            static value(node) {
                return { url: node.getAttribute('data-url') };
            }
        }
        LinkBlot.blotName = 'url-link';
        LinkBlot.tagName = 'span';
        LinkBlot.className = 'link-embed';
        Quill.register(LinkBlot);

        class PDFBlot extends Embed {
            static create(value) {
                const node = super.create();
                node.setAttribute('data-url', value.url);
                node.setAttribute('data-filename', value.filename);
                node.setAttribute('contenteditable', 'false');
                const shortName = value.filename.length > 15
                    ? value.filename.substring(0, 12) + '...' + value.filename.slice(-4)
                    : value.filename;
                node.innerHTML = `<span class="pdf-embed-icon">PDF</span><span class="pdf-embed-label">${shortName}</span>`;
                node.onclick = () => {
                    if (value.url) window.open(value.url, '_blank');
                };
                return node;
            }
            static value(node) {
                return {
                    url: node.getAttribute('data-url'),
                    filename: node.getAttribute('data-filename')
                };
            }
        }
        PDFBlot.blotName = 'pdf';
        PDFBlot.tagName = 'span';
        PDFBlot.className = 'pdf-embed';
        Quill.register(PDFBlot);

        this.createQuillInstance();
    },

    /**
     * Create the Quill instance and wire its event handlers.
     */
    createQuillInstance() {
        this.editor = new Quill('#edit-editor', {
            theme: 'snow',
            placeholder: 'Start writing...',
            modules: {
                toolbar: false,
                keyboard: { bindings: {} }
            }
        });

        this.setupEditorShortcuts();

        this.editor.on('text-change', () => {
            this.triggerAutoSave();
        });

        // Capture phase fires before Quill's bubbling paste handler, so
        // stopImmediatePropagation prevents Quill from inserting the raw URL text.
        this.editor.root.addEventListener('paste', (e) => {
            this.handlePaste(e);
        }, true);

        this.editor.root.addEventListener('click', (e) => {
            const ytEmbed = e.target.closest('.youtube-embed');
            if (ytEmbed) {
                const url = ytEmbed.getAttribute('data-url');
                if (url) {
                    navigator.clipboard.writeText(url);
                    window.open(url, '_blank', 'noopener,noreferrer');
                    ytEmbed.style.borderColor = '#4a9eff';
                    setTimeout(() => { ytEmbed.style.borderColor = ''; }, 500);
                }
                return;
            }

            const pdfEmbed = e.target.closest('.pdf-embed');
            if (pdfEmbed) {
                const url = pdfEmbed.getAttribute('data-url');
                if (url) window.open(url, '_blank');
                return;
            }
        });

        this.editor.on('text-change', (delta, oldDelta, source) => {
            if (source === 'user') this.checkForBabelMention();
        });
    },

    /**
     * Bind keyboard shortcuts onto the Quill root element.
     */
    setupEditorShortcuts() {
        const editor = this.editor;

        const toggleFormat = (format) => {
            const range = editor.getSelection();
            if (range) {
                const currentFormat = editor.getFormat(range);
                editor.format(format, !currentFormat[format]);
            }
        };

        editor.root.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'b') {
                e.preventDefault();
                toggleFormat('bold');
            } else if (e.ctrlKey && e.key === 'i') {
                e.preventDefault();
                toggleFormat('italic');
            } else if (e.ctrlKey && e.shiftKey && e.key === 'S') {
                e.preventDefault();
                toggleFormat('strike');
            } else if (e.ctrlKey && e.key === 'h') {
                e.preventDefault();
                toggleFormat('highlight');
            } else if (e.ctrlKey && e.shiftKey && e.key === 'L') {
                e.preventDefault();
                toggleFormat('list', 'bullet');
            } else if (e.ctrlKey && e.shiftKey && e.key === 'C') {
                e.preventDefault();
                toggleFormat('code-block');
            } else if (e.ctrlKey && (e.key === '=' || e.key === '+')) {
                e.preventDefault();
                this.cycleHeading(-1);
            } else if (e.ctrlKey && e.key === '-') {
                e.preventDefault();
                this.cycleHeading(1);
            }
        });
    },

    /**
     * Cycle heading level at the current selection (0 → 1 → 2 → 3 → 0).
     * direction: 1 = increase level number, -1 = decrease.
     */
    cycleHeading(direction) {
        const range = this.editor.getSelection();
        if (!range) return;

        const format = this.editor.getFormat(range);
        let currentLevel = format.header ? format.header : 0;

        let newLevel = currentLevel + direction;
        if (newLevel > 3) newLevel = 0;
        if (newLevel < 0) newLevel = 3;

        this.editor.format('header', newLevel === 0 ? false : newLevel);
    },

    /**
     * Handle paste events: auto-embed YouTube URLs and PDFs.
     * Registered in capture phase so it fires before Quill's handler.
     * stopImmediatePropagation prevents Quill from inserting raw text.
     */
    handlePaste(e) {
        const text = e.clipboardData?.getData('text/plain')?.trim();
        if (text && this.isURL(text)) {
            e.preventDefault();
            e.stopImmediatePropagation();
            this.embedLink(text);
            return;
        }

        const files = e.clipboardData?.files;
        if (files && files.length > 0) {
            for (const file of files) {
                if (file.type === 'application/pdf') {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    this.embedPDF(file);
                    return;
                }
            }
        }
    },

    isURL(url) {
        const regularUrlRegex = /^(https?:\/\/)?(www\.)?[\w\-]+(\.[\w\-]+)+[/#?]?.*$/i;
        return regularUrlRegex.test(url);
    },

    embedLink(url) {
        if (!url) return;
        const range = this.editor.getSelection(true);
        this.editor.insertEmbed(range.index, 'url-link', { url }, 'user');
        this.editor.setSelection(range.index + 1);
    },

    /**
     * Embed a PDF File as an inline pill.
     * TODO: store in IndexedDB with device ID for persistence across sessions.
     */
    embedPDF(file) {
        const fileUrl = URL.createObjectURL(file);
        const range = this.editor.getSelection(true);
        this.editor.insertEmbed(range.index, 'pdf', { url: fileUrl, filename: file.name }, 'user');
        this.editor.setSelection(range.index + 1);
    },

    /**
     * Detect @ trigger and open babel selector.
     * TODO: implement neighbor/nested babel recommendations.
     */
    checkForBabelMention() {
        const range = this.editor.getSelection();
        if (!range) return;

        const text = this.editor.getText();
        const cursorPos = range.index;

        let atPos = -1;
        for (let i = cursorPos - 1; i >= 0; i--) {
            if (text[i] === '@') { atPos = i; break; }
            if (text[i] === ' ' || text[i] === '\n') break;
        }

        if (atPos >= 0) this.showBabelSelector(atPos);
    },

    /**
     * Show babel selector popup at atPosition.
     * TODO: implement popup UI with real recommendations.
     */
    showBabelSelector(atPosition) {
        const recommendations = this.getRecommendedBabels();
        if (recommendations.length === 0) {
            console.log('No babels found'); // placeholder
        }
    },

    /**
     * Returns recommended babels for @ linking.
     * TODO: return neighbor/nested babels.
     */
    getRecommendedBabels() {
        return [];
    },

    /**
     * Debounce auto-save by 2 seconds (silent — no visible indicator).
     */
    triggerAutoSave() {
        if (this.autoSaveTimer) clearTimeout(this.autoSaveTimer);
        this.autoSaveTimer = setTimeout(() => this.saveCurrentBabel(), 2000);
    },

    /**
     * Flush current editor content into State and persist.
     */
    saveCurrentBabel() {
        const babel = State.editingBabel;
        if (!babel) return;

        babel.title = UI.elements.editTitle.value.trim() || 'Untitled';
        babel.description = this.editor ? this.editor.root.innerHTML : '';
        babel.contentDelta = this.editor ? this.editor.getContents() : null;
        babel.color = State.selectedColor;

        Persistence.save();
    }
};
