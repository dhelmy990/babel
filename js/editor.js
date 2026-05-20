// ============================================
// QUILL EDITOR
// ============================================

const Editor = {
    editor: null,
    autoSaveTimer: null,
    currentHeadingLevel: 0,
    blotsRegistered: false,
    _missingFileModal: null,

    initEditor() {
        if (this.editor) return;

        const Inline = Quill.import('blots/inline');
        const Embed = Quill.import('blots/embed');
        const BlockEmbed = Quill.import('blots/block/embed');

        if (this.blotsRegistered) {
            this.createQuillInstance();
            return;
        }
        this.blotsRegistered = true;

        // ── Highlight ─────────────────────────────────────────────────────────
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

        // ── URL link (inline pill) ────────────────────────────────────────────
        class LinkBlot extends Embed {
            static create(value) {
                const node = super.create();
                node.setAttribute('data-url', value.url);
                node.setAttribute('contenteditable', 'false');
                const domain = value.url.replace(/^(https?:\/\/)?(www\.)?/, '').split('/')[0];
                const faviconUrl = `https://www.google.com/s2/favicons?sz=32&domain=${domain}`;
                node.innerHTML = `<img src="${faviconUrl}" alt="" style="width:16px;height:16px;vertical-align:middle;margin-right:6px;border-radius:2px;"><span class="link-embed-label">link</span>`;
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

        // ── PDF (inline pill, opens local file) ───────────────────────────────
        class PDFBlot extends Embed {
            static create(value) {
                const node = super.create();
                // Support old 'url' key from before Electron migration
                const filePath = value.path || value.url || '';
                node.setAttribute('data-path', filePath);
                node.setAttribute('contenteditable', 'false');

                const filename = value.filename || filePath.split(/[\\/]/).pop() || 'Document';
                const shortName = filename.length > 15
                    ? filename.substring(0, 12) + '...' + filename.slice(-4)
                    : filename;
                node.innerHTML = `<span class="pdf-embed-icon">PDF</span><span class="pdf-embed-label">${shortName}</span>`;

                node.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const p = node.getAttribute('data-path');
                    if (!p) return;
                    // blob: URLs are in-memory fallbacks — open directly, no existence check
                    if (p.startsWith('blob:')) { window.open(p, '_blank'); return; }
                    if (window.electronAPI) {
                        const exists = await window.electronAPI.checkFileExists(p);
                        if (exists) {
                            window.electronAPI.openFile(p);
                        } else {
                            Editor.showMissingFileModal(node);
                        }
                    } else {
                        window.open(p, '_blank');
                    }
                });
                return node;
            }
            static value(node) {
                return {
                    path: node.getAttribute('data-path'),
                    filename: node.querySelector('.pdf-embed-label')?.textContent || ''
                };
            }
        }
        PDFBlot.blotName = 'pdf';
        PDFBlot.tagName = 'span';
        PDFBlot.className = 'pdf-embed';
        Quill.register(PDFBlot);

        // ── Image (block embed, loaded via file:// — same origin as app) ────────
        class ImageBlot extends BlockEmbed {
            static create(value) {
                const node = super.create();
                node.setAttribute('data-path', value.path);
                node.setAttribute('contenteditable', 'false');
                const img = document.createElement('img');
                img.src = 'file://' + value.path.replace(/\\/g, '/');
                img.draggable = false;
                img.alt = '';
                node.appendChild(img);
                return node;
            }
            static value(node) {
                return { path: node.getAttribute('data-path') };
            }
        }
        ImageBlot.blotName = 'local-image';
        ImageBlot.tagName = 'div';
        ImageBlot.className = 'image-embed';
        Quill.register(ImageBlot);

        this.createQuillInstance();
    },

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

        this.editor.on('text-change', (delta, oldDelta, source) => {
            if (source === 'user') this.checkForBabelMention();
        });

        // Capture phase fires before Quill's paste handler
        this.editor.root.addEventListener('paste', (e) => {
            this.handlePaste(e);
        }, true);  // capture phase — fires before Quill's paste handler

        // Drag-and-drop for images and PDFs
        this.editor.root.addEventListener('dragover', (e) => {
            if (e.dataTransfer.types.includes('Files')) e.preventDefault();
        });

        this.editor.root.addEventListener('drop', (e) => {
            e.preventDefault();
            const files = e.dataTransfer.files;
            for (const file of files) {
                if (file.type.startsWith('image/')) {
                    this.embedImage(file);
                    return;
                }
                if (file.type === 'application/pdf') {
                    this.embedPDF(file);
                    return;
                }
            }
        });
    },

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
                    this.embedPDF(file); // async, intentionally not awaited here
                    return;
                }
                if (file.type.startsWith('image/')) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    this.embedImage(file); // async, intentionally not awaited here
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

    async embedPDF(file) {
        const range = this.editor.getSelection(true);
        let filePath = file.path || '';
        if (!filePath && window.electronAPI) {
            const ext = file.name.split('.').pop() || 'pdf';
            const buf = await file.arrayBuffer();
            const result = await window.electronAPI.saveFile(buf, 'pdfs', ext);
            filePath = result.success ? result.path : URL.createObjectURL(file);
        } else if (!filePath) {
            filePath = URL.createObjectURL(file);
        }
        this.editor.insertEmbed(range.index, 'pdf', { path: filePath, filename: file.name }, 'user');
        this.editor.setSelection(range.index + 1);
    },

    async embedImage(file) {
        if (!window.electronAPI) return;
        const range = this.editor.getSelection(true);
        let filePath = file.path || '';
        if (!filePath) {
            const ext = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
            const buf = await file.arrayBuffer();
            const result = await window.electronAPI.saveFile(buf, 'images', ext);
            if (!result.success) return;
            filePath = result.path;
        }
        this.editor.insertEmbed(range.index, 'local-image', { path: filePath }, 'user');
        this.editor.setSelection(range.index + 1);
    },

    showMissingFileModal(node) {
        this.hideMissingFileModal();

        const modal = document.createElement('div');
        modal.className = 'missing-file-modal';
        modal.innerHTML = `
            <div class="missing-file-text">File not found</div>
            <button class="missing-file-locate">Locate File</button>
        `;

        const rect = node.getBoundingClientRect();
        modal.style.left = rect.left + 'px';
        modal.style.top = (rect.bottom + 6) + 'px';

        modal.querySelector('.missing-file-locate').addEventListener('click', async () => {
            const result = await window.electronAPI.locateFile([
                { name: 'PDF', extensions: ['pdf'] }
            ]);
            if (result.success) {
                node.setAttribute('data-path', result.path);
                const label = node.querySelector('.pdf-embed-label');
                if (label) {
                    const filename = result.path.split(/[\\/]/).pop();
                    label.textContent = filename.length > 15
                        ? filename.substring(0, 12) + '...' + filename.slice(-4)
                        : filename;
                }
                Editor.triggerAutoSave();
            }
            this.hideMissingFileModal();
        });

        document.body.appendChild(modal);
        this._missingFileModal = modal;

        // Close on outside click
        const closeOnOutside = (e) => {
            if (!modal.contains(e.target) && e.target !== node) {
                this.hideMissingFileModal();
                document.removeEventListener('click', closeOnOutside);
            }
        };
        setTimeout(() => document.addEventListener('click', closeOnOutside), 0);
    },

    hideMissingFileModal() {
        if (this._missingFileModal) {
            this._missingFileModal.remove();
            this._missingFileModal = null;
        }
    },

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

    showBabelSelector(atPosition) {
        const recommendations = this.getRecommendedBabels();
        if (recommendations.length === 0) {
            console.log('No babels found');
        }
    },

    getRecommendedBabels() {
        return [];
    },

    triggerAutoSave() {
        if (this.autoSaveTimer) clearTimeout(this.autoSaveTimer);
        this.autoSaveTimer = setTimeout(() => this.saveCurrentBabel(), 2000);
    },

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
