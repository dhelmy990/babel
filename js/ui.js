// ============================================
// UI HANDLERS
// ============================================

const UI = {
    // DOM element references (set during init)
    elements: {},

    // Handle returned by Rendering.createPreview
    preview: null,

    /**
     * Helper: Safely check if element has a class (null-safe)
     */
    hasClass(element, className) {
        return element?.classList?.contains(className) ?? false;
    },

    /**
     * Initialize DOM element references
     */
    init() {
        this.elements = {
            graphContainer: document.getElementById('graph-container'),
            hintText: document.getElementById('hint-text'),
            creationOverlay: document.getElementById('creation-overlay'),
            creationCircle: document.getElementById('creation-circle'),
            creationForm: document.getElementById('creation-form'),
            colorPalette: document.getElementById('color-palette'),
            similarBabels: document.getElementById('similar-babels'),
            comparisonOverlay: document.getElementById('comparison-overlay'),
            editOverlay: document.getElementById('edit-overlay'),
            saveToast: document.getElementById('save-toast'),
            editTitle: document.getElementById('edit-title'),
            editEditor: document.getElementById('edit-editor'),
            editColorPalette: document.getElementById('edit-color-palette'),
            editHelpBtn: document.getElementById('edit-help-btn'),
            editShortcutsPopup: document.getElementById('edit-shortcuts-popup'),
            editBabel3d: document.getElementById('edit-babel-3d')
        };
    },

    /**
     * Setup color palette swatches
     */
    setupColorPalette() {
        const { colorPalette, editColorPalette } = this.elements;

        if (colorPalette) {
            colorPalette.innerHTML = '';
            Config.colors.bright.forEach((color, index) => {
                const swatch = this.createColorSwatch(color, index === 0, colorPalette, 'creation');
                colorPalette.appendChild(swatch);
            });
        }

        if (editColorPalette) {
            editColorPalette.innerHTML = '';
            Config.colors.bright.forEach((color, index) => {
                const swatch = this.createColorSwatch(color, false, editColorPalette, 'edit');
                editColorPalette.appendChild(swatch);
            });
        }
    },

    createColorSwatch(color, selected, palette, mode) {
        const swatch = document.createElement('div');
        swatch.className = 'color-swatch' + (selected ? ' selected' : '');
        swatch.style.backgroundColor = color;
        swatch.addEventListener('click', () => {
            State.selectedColor = color;
            palette.querySelectorAll('.color-swatch').forEach((s) => {
                s.classList.toggle('selected', s.style.backgroundColor === color);
            });
            if (mode === 'edit') {
                this.updatePreviewColor(color);
                Editor.triggerAutoSave();
            }
        });
        return swatch;
    },

    initPreview(color) {
        const container = this.elements.editBabel3d;
        if (!container) return;
        this.cleanupPreview();
        this.preview = Rendering.createPreview(container, color);
    },

    updatePreviewColor(color) {
        this.preview?.updateColor(color);
    },

    cleanupPreview() {
        this.preview?.destroy();
        this.preview = null;
    },

    setupEventListeners() {
        document.addEventListener('keydown', (e) => this.handleKeyDown(e));
        document.addEventListener('keyup', (e) => this.handleKeyUp(e));

        document.getElementById('create-babel-btn').addEventListener('click', () => {
            document.dispatchEvent(new CustomEvent('babel:create'));
        });
        document.getElementById('cancel-create-btn').addEventListener('click', () => this.cancelCreation());
        document.getElementById('comparison-done-btn').addEventListener('click', () => this.closeComparison());
        document.getElementById('edit-done-btn').addEventListener('click', () => this.closeEdit());

        document.querySelector('.edge-arrow.left-to-right').addEventListener('click', () => {
            document.dispatchEvent(new CustomEvent('babel:toggle-edge', { detail: { direction: 'left-to-right' } }));
        });
        document.querySelector('.edge-arrow.right-to-left').addEventListener('click', () => {
            document.dispatchEvent(new CustomEvent('babel:toggle-edge', { detail: { direction: 'right-to-left' } }));
        });

        document.addEventListener('keydown', e => {
            if (e.ctrlKey && e.key === 's') {
                e.preventDefault();
                document.dispatchEvent(new CustomEvent('babel:save'));
            }
        });

        const helpBtn = this.elements.editHelpBtn;
        const shortcutsPopup = this.elements.editShortcutsPopup;
        if (helpBtn && shortcutsPopup) {
            helpBtn.addEventListener('click', () => {
                shortcutsPopup.classList.toggle('visible');
            });
            document.addEventListener('click', (e) => {
                if (!helpBtn.contains(e.target) && !shortcutsPopup.contains(e.target)) {
                    shortcutsPopup.classList.remove('visible');
                }
            });
        }

        const editTitle = this.elements.editTitle;
        if (editTitle) {
            editTitle.addEventListener('input', () => Editor.triggerAutoSave());
        }
    },

    handleKeyDown(e) {
        const { comparisonOverlay, editOverlay, creationForm } = this.elements;

        if (this.hasClass(editOverlay, 'active')) {
            if (e.key === 'Escape') this.closeEdit();
            return;
        }

        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            if (e.key === 'Escape') e.target.blur();
            if (e.key === 'Enter' && this.hasClass(creationForm, 'active')) {
                e.preventDefault();
                document.dispatchEvent(new CustomEvent('babel:create'));
            }
            return;
        }

        switch (e.key.toLowerCase()) {
            case 'r':
                if (!State.isCreating &&
                    !this.hasClass(comparisonOverlay, 'active') &&
                    !this.hasClass(editOverlay, 'active')) {
                    this.startCreation();
                }
                break;
            case 'escape':
                if (this.hasClass(creationForm, 'active')) {
                    this.cancelCreation();
                } else if (this.hasClass(comparisonOverlay, 'active')) {
                    this.closeComparison();
                } else if (this.hasClass(editOverlay, 'active')) {
                    this.closeEdit();
                } else if (State.selectedBabel) {
                    document.dispatchEvent(new CustomEvent('babel:deselect'));
                }
                break;
            case 'delete':
            case 'backspace':
                if (State.selectedBabel &&
                    !this.hasClass(comparisonOverlay, 'active') &&
                    !this.hasClass(editOverlay, 'active')) {
                    document.dispatchEvent(new CustomEvent('babel:delete'));
                }
                break;
            case 'e':
                if (State.selectedBabel &&
                    !this.hasClass(comparisonOverlay, 'active') &&
                    !this.hasClass(editOverlay, 'active')) {
                    this.openEdit(State.selectedBabel);
                }
                break;
            case ' ':
                e.preventDefault();
                document.dispatchEvent(new CustomEvent('babel:reset-camera'));
                break;
        }
    },

    handleKeyUp(e) {
        if (e.key.toLowerCase() === 'r' &&
            State.isCreating &&
            !this.elements.creationForm.classList.contains('active')) {
            this.cancelCreation();
        }
    },

    /**
     * Creation animation
     */
    startCreation() {
        State.isCreating = true;
        State.creationStartTime = Date.now();
        this.elements.creationOverlay.classList.add('active');
        this.animateCreation();
    },

    animateCreation() {
        if (!State.isCreating) return;

        const elapsed = Date.now() - State.creationStartTime;
        const progress = Math.min(elapsed / Config.ui.creationHoldDuration, 1);
        const { creationCircle } = this.elements;

        const maxSize = Math.max(window.innerWidth, window.innerHeight) * 2;
        const size = progress * maxSize * 0.1;
        creationCircle.style.width = size + 'px';
        creationCircle.style.height = size + 'px';

        const colorIndex = Math.floor((elapsed / 300) % Config.colors.bright.length);
        creationCircle.style.backgroundColor = Config.colors.bright[colorIndex];

        if (progress >= 1) {
            this.showCreationForm();
        } else {
            State.creationAnimationFrame = requestAnimationFrame(() => this.animateCreation());
        }
    },

    showCreationForm() {
        const { creationOverlay, creationCircle, creationForm } = this.elements;

        creationOverlay.classList.remove('active');
        creationCircle.style.width = '0';
        creationCircle.style.height = '0';

        this.updateSimilarBabelsSelector();

        document.getElementById('babel-title').value = '';
        document.getElementById('babel-description').value = '';
        State.selectedColor = Config.colors.bright[0];
        State.selectedSimilarBabels = [];
        this.setupColorPalette();

        creationForm.classList.add('active');
    },

    updateSimilarBabelsSelector() {
        const { similarBabels } = this.elements;
        similarBabels.innerHTML = '';

        if (State.babels.length === 0) {
            similarBabels.innerHTML = '<span class="babel-chips-empty">No other babels yet</span>';
            return;
        }

        State.babels.forEach(babel => {
            const chip = document.createElement('div');
            chip.className = 'babel-chip';
            chip.textContent = babel.title || 'Untitled';
            chip.style.borderColor = babel.color;
            chip.addEventListener('click', () => {
                chip.classList.toggle('selected');
                if (chip.classList.contains('selected')) {
                    State.selectedSimilarBabels.push(babel.id);
                } else {
                    State.selectedSimilarBabels = State.selectedSimilarBabels.filter(id => id !== babel.id);
                }
            });
            similarBabels.appendChild(chip);
        });
    },

    cancelCreation() {
        State.isCreating = false;
        if (State.creationAnimationFrame) {
            cancelAnimationFrame(State.creationAnimationFrame);
        }
        this.animateReverse(parseFloat(this.elements.creationCircle.style.width) || 0);
    },

    animateReverse(size) {
        if (size <= 0) {
            this.elements.creationOverlay.classList.remove('active');
            this.elements.creationForm.classList.remove('active');
            return;
        }

        const newSize = size - (size * 0.1 + 10);
        this.elements.creationCircle.style.width = Math.max(0, newSize) + 'px';
        this.elements.creationCircle.style.height = Math.max(0, newSize) + 'px';
        requestAnimationFrame(() => this.animateReverse(newSize));
    },

    /**
     * Update hint text visibility
     */
    updateHintText() {
        this.elements.hintText.classList.toggle('hidden', State.babels.length > 0);
    },

    /**
     * Show save toast
     */
    showSaveToast() {
        this.elements.saveToast.classList.add('visible');
        setTimeout(() => {
            this.elements.saveToast.classList.remove('visible');
        }, Config.ui.toastDuration);
    },

    /**
     * Comparison mode
     */
    openComparison() {
        const [left, right] = State.comparisonBabels;
        const { comparisonOverlay } = this.elements;

        const leftPanel = document.getElementById('babel-panel-left');
        leftPanel.querySelector('.babel-sphere').style.backgroundColor = left.color;
        leftPanel.querySelector('.babel-edit-title').value = left.title;
        leftPanel.querySelector('.babel-edit-description').value = left.description;
        leftPanel.dataset.babelId = left.id;

        const rightPanel = document.getElementById('babel-panel-right');
        rightPanel.querySelector('.babel-sphere').style.backgroundColor = right.color;
        rightPanel.querySelector('.babel-edit-title').value = right.title;
        rightPanel.querySelector('.babel-edit-description').value = right.description;
        rightPanel.dataset.babelId = right.id;

        this.updateEdgeIndicators();
        comparisonOverlay.classList.add('active');

        setTimeout(() => {
            leftPanel.classList.add('open');
            rightPanel.classList.add('open');
        }, Config.ui.panelAnimationDelay);
    },

    updateEdgeIndicators() {
        const [left, right] = State.comparisonBabels;
        const leftToRight = State.hasEdge(left.id, right.id);
        const rightToLeft = State.hasEdge(right.id, left.id);

        document.querySelector('.edge-arrow.left-to-right').classList.toggle('active', leftToRight);
        document.querySelector('.edge-arrow.right-to-left').classList.toggle('active', rightToLeft);
    },

    closeComparison() {
        const leftPanel = document.getElementById('babel-panel-left');
        const rightPanel = document.getElementById('babel-panel-right');

        const leftBabel = State.getBabel(leftPanel.dataset.babelId);
        const rightBabel = State.getBabel(rightPanel.dataset.babelId);

        if (leftBabel) {
            leftBabel.title = leftPanel.querySelector('.babel-edit-title').value.trim() || 'Untitled';
            leftBabel.description = leftPanel.querySelector('.babel-edit-description').value.trim();
        }

        if (rightBabel) {
            rightBabel.title = rightPanel.querySelector('.babel-edit-title').value.trim() || 'Untitled';
            rightBabel.description = rightPanel.querySelector('.babel-edit-description').value.trim();
        }

        leftPanel.classList.remove('open');
        rightPanel.classList.remove('open');

        setTimeout(() => {
            this.elements.comparisonOverlay.classList.remove('active');
            State.comparisonBabels = [];
            document.dispatchEvent(new CustomEvent('babel:comparison-closed'));
        }, Config.ui.panelCloseDelay);
    },

    /**
     * Edit mode
     */
    openEdit(babel) {
        State.editingBabel = babel;
        State.selectedColor = babel.color;

        Editor.initEditor();

        this.elements.editTitle.value = babel.title || '';

        if (Editor.editor) {
            Editor.editor.root.innerHTML = babel.description || '';
        }

        this.setupColorPalette();
        const colorPalette = this.elements.editColorPalette;
        if (colorPalette) {
            colorPalette.querySelectorAll('.color-swatch').forEach((swatch) => {
                swatch.classList.toggle('selected', swatch.style.backgroundColor === babel.color);
            });
        }

        this.elements.editOverlay.classList.add('active');

        setTimeout(() => this.initPreview(babel.color), 50);
        setTimeout(() => this.elements.editTitle.focus(), 100);
    },

    closeEdit() {
        Editor.saveCurrentBabel();

        if (Editor.autoSaveTimer) {
            clearTimeout(Editor.autoSaveTimer);
            Editor.autoSaveTimer = null;
        }

        this.cleanupPreview();

        if (this.elements.editShortcutsPopup) {
            this.elements.editShortcutsPopup.classList.remove('visible');
        }

        this.elements.editOverlay.classList.remove('active');
        Rendering.textureCache.clear();
        State.editingBabel = null;

        document.dispatchEvent(new CustomEvent('babel:edit-closed'));
    }
};
