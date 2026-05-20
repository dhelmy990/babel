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

        class YouTubeBlot extends Embed {
            static create(value) {
                const node = super.create();
                node.setAttribute('data-url', value.url);
                node.setAttribute('contenteditable', 'false');
                node.innerHTML = `<span class="youtube-embed-icon">▶</span><span class="youtube-embed-label">YouTube</span>`;
                node.onclick = () => {
                    navigator.clipboard.writeText(value.url);
                    node.style.borderColor = '#4a9eff';
                    setTimeout(() => { node.style.borderColor = ''; }, 500);
                    window.open(value.url, '_blank', 'noopener,noreferrer');
                };
                return node;
            }
            static value(node) {
                return { url: node.getAttribute('data-url') };
            }
        }
        YouTubeBlot.blotName = 'youtube';
        YouTubeBlot.tagName = 'span';
        YouTubeBlot.className = 'youtube-embed';
        Quill.register(YouTubeBlot);

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

        this.editor.root.addEventListener('paste', (e) => {
            this.handlePaste(e);
        });

        this.editor.root.addEventListener('click', (e) => {
            const ytEmbed = e.target.closest('.youtube-embed');
            if (ytEmbed) {
                const url = ytEmbed.getAttribute('data-url');
                if (url) {
                    navigator.clipboard.writeText(url);
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
     * Handle paste events: auto-embed PDFs and YouTube URLs.
     */
    handlePaste(e) {
        const files = e.clipboardData?.files;
        if (files && files.length > 0) {
            for (const file of files) {
                if (file.type === 'application/pdf') {
                    e.preventDefault();
                    this.embedPDF(file);
                    return;
                }
            }
        }

        const text = e.clipboardData?.getData('text/plain');
        if (text && this.isYouTubeURL(text)) {
            e.preventDefault();
            this.embedYouTube(text);
            e.preventDefault();
        }
    },

    isYouTubeURL(url) {
        const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)[\w-]+/;
        return youtubeRegex.test(url);
    },

    getYouTubeID(url) {
        const match = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([\w-]+)/);
        return match ? match[1] : null;
    },

    embedYouTube(url) {
        const videoId = this.getYouTubeID(url);
        if (!videoId) return;
        const range = this.editor.getSelection(true);
        this.editor.insertEmbed(range.index, 'youtube', { url }, 'user');
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
        babel.color = State.selectedColor;

        Persistence.save();
    }
};
