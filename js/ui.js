// ============================================
// UI HANDLERS
// ============================================

const UI = {
    // DOM element references (set during init)
    elements: {},

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
            editPalette: document.getElementById('edit-color-palette')
        };
    },

    /**
     * Setup color palette swatches
     */
    setupColorPalette() {
        const { colorPalette, editPalette } = this.elements;

        // Creation palette
        colorPalette.innerHTML = '';
        Config.colors.bright.forEach((color, index) => {
            const swatch = this.createColorSwatch(color, index === 0, colorPalette);
            colorPalette.appendChild(swatch);
        });

        // Edit palette
        editPalette.innerHTML = '';
        Config.colors.bright.forEach((color, index) => {
            const swatch = this.createColorSwatch(color, false, editPalette);
            editPalette.appendChild(swatch);
        });
    },

    createColorSwatch(color, selected, palette) {
        const swatch = document.createElement('div');
        swatch.className = 'color-swatch' + (selected ? ' selected' : '');
        swatch.style.backgroundColor = color;
        swatch.addEventListener('click', () => {
            const index = Config.colors.bright.indexOf(color);
            State.selectedColor = color;
            palette.querySelectorAll('.color-swatch').forEach((s, i) => {
                s.classList.toggle('selected', i === index);
            });
        });
        return swatch;
    },

    /**
     * Setup event listeners
     */
    setupEventListeners(callbacks) {
        document.addEventListener('keydown', (e) => this.handleKeyDown(e, callbacks));
        document.addEventListener('keyup', (e) => this.handleKeyUp(e, callbacks));

        document.getElementById('create-babel-btn').addEventListener('click', callbacks.createBabel);
        document.getElementById('cancel-create-btn').addEventListener('click', callbacks.cancelCreation);
        document.getElementById('comparison-done-btn').addEventListener('click', callbacks.closeComparison);
        document.getElementById('edit-done-btn').addEventListener('click', callbacks.closeEdit);

        document.querySelector('.edge-arrow.left-to-right')
            .addEventListener('click', () => callbacks.toggleEdge('left-to-right'));
        document.querySelector('.edge-arrow.right-to-left')
            .addEventListener('click', () => callbacks.toggleEdge('right-to-left'));

        document.addEventListener('keydown', e => {
            if (e.ctrlKey && e.key === 's') {
                e.preventDefault();
                callbacks.save();
            }
        });
    },

    handleKeyDown(e, callbacks) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            if (e.key === 'Escape') e.target.blur();
            if (e.key === 'Enter' && this.elements.creationForm.classList.contains('active')) {
                e.preventDefault();
                callbacks.createBabel();
            }
            return;
        }

        const { comparisonOverlay, editOverlay, creationForm } = this.elements;

        switch (e.key.toLowerCase()) {
            case 'r':
                if (!State.isCreating &&
                    !comparisonOverlay.classList.contains('active') &&
                    !editOverlay.classList.contains('active')) {
                    callbacks.startCreation();
                }
                break;
            case 'escape':
                if (creationForm.classList.contains('active')) {
                    callbacks.cancelCreation();
                } else if (comparisonOverlay.classList.contains('active')) {
                    callbacks.closeComparison();
                } else if (editOverlay.classList.contains('active')) {
                    callbacks.closeEdit();
                } else if (State.selectedBabel) {
                    callbacks.deselectBabel();
                }
                break;
            case 'delete':
            case 'backspace':
                if (State.selectedBabel &&
                    !comparisonOverlay.classList.contains('active') &&
                    !editOverlay.classList.contains('active')) {
                    callbacks.handleDelete();
                }
                break;
            case 'e':
                if (State.selectedBabel &&
                    !comparisonOverlay.classList.contains('active') &&
                    !editOverlay.classList.contains('active')) {
                    callbacks.openEdit(State.selectedBabel);
                }
                break;
            case ' ':
                e.preventDefault();
                callbacks.resetCamera();
                break;
        }
    },

    handleKeyUp(e, callbacks) {
        if (e.key.toLowerCase() === 'r' &&
            State.isCreating &&
            !this.elements.creationForm.classList.contains('active')) {
            callbacks.cancelCreation();
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

        const colorIndex = Math.floor((elapsed / 300) % Config.colors.dark.length);
        creationCircle.style.backgroundColor = Config.colors.dark[colorIndex];

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

    closeComparison(callback) {
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
            if (callback) callback();
        }, Config.ui.panelCloseDelay);
    },

    /**
     * Edit mode
     */
    openEdit(babel) {
        State.editingBabel = babel;
        const panel = document.getElementById('edit-babel-panel');

        panel.querySelector('.babel-sphere').style.backgroundColor = babel.color;
        document.getElementById('edit-title').value = babel.title;
        document.getElementById('edit-description').value = babel.description;

        const colorIndex = Config.colors.bright.indexOf(babel.color);
        this.elements.editPalette.querySelectorAll('.color-swatch').forEach((s, i) => {
            s.classList.toggle('selected', i === colorIndex);
        });
        State.selectedColor = babel.color;

        this.elements.editOverlay.classList.add('active');

        setTimeout(() => {
            panel.classList.add('open');
        }, Config.ui.panelAnimationDelay);
    },

    closeEdit(callback) {
        const babel = State.editingBabel;
        if (babel) {
            babel.title = document.getElementById('edit-title').value.trim() || 'Untitled';
            babel.description = document.getElementById('edit-description').value.trim();
            babel.color = State.selectedColor;
        }

        const panel = document.getElementById('edit-babel-panel');
        panel.classList.remove('open');

        setTimeout(() => {
            this.elements.editOverlay.classList.remove('active');
            State.editingBabel = null;
            if (callback) callback();
        }, Config.ui.panelCloseDelay);
    }
};
