// ============================================
// UI HANDLERS
// ============================================

const UI = {
    // DOM element references (set during init)
    elements: {},

    // Handle returned by Rendering.createPreview
    preview: null,

    profiles: [],
    activeProfileIndex: 0,
    profileRequestVersion: 0,
    profileRequestPending: false,
    retryProfileAction: null,

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
            editBabel3d: document.getElementById('edit-babel-3d'),
            profileSelector: document.getElementById('profile-selector'),
            profileWheel: document.getElementById('profile-wheel'),
            profileWheelList: document.getElementById('profile-wheel-list'),
            profileSelectorStatus: document.getElementById('profile-selector-status'),
            profileSelectorRetry: document.getElementById('profile-selector-retry'),
            graphProfileBar: document.getElementById('graph-profile-bar'),
            graphProfileName: document.getElementById('graph-profile-name'),
            switchProfileBtn: document.getElementById('switch-profile-btn')
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
            if (!ProfileSelector.canMutate(State)) return;
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
        this.elements.profileSelectorRetry.addEventListener('click', () => this.retryProfileAction?.());
        this.elements.switchProfileBtn.addEventListener('click', () => this.showProfileSelector());
        this.elements.profileWheel.addEventListener('wheel', (event) => {
            event.preventDefault();
            const direction = ProfileSelector.wheelDirection(event.deltaX, event.deltaY);
            if (direction !== 0) this.moveActiveProfile(direction);
        }, { passive: false });

        document.querySelector('.edge-arrow.left-to-right').addEventListener('click', () => {
            document.dispatchEvent(new CustomEvent('babel:toggle-edge', { detail: { direction: 'left-to-right' } }));
        });
        document.querySelector('.edge-arrow.right-to-left').addEventListener('click', () => {
            document.dispatchEvent(new CustomEvent('babel:toggle-edge', { detail: { direction: 'right-to-left' } }));
        });

        document.addEventListener('keydown', e => {
            if (e.ctrlKey && e.key === 's') {
                e.preventDefault();
                if (!this.elements.profileSelector.classList.contains('hidden')) return;
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

        if (!this.elements.profileSelector.classList.contains('hidden')) {
            if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault();
                this.moveActiveProfile(e.key === 'ArrowDown' ? 1 : -1);
            } else if (e.key === 'Enter' && this.profiles[this.activeProfileIndex]) {
                e.preventDefault();
                this.selectProfile(this.profiles[this.activeProfileIndex]);
            }
            return;
        }

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
                if (ProfileSelector.canMutate(State) &&
                    !State.isCreating &&
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
                if (ProfileSelector.canMutate(State) &&
                    State.selectedBabel &&
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
        if (!ProfileSelector.canMutate(State)) return;
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
        this.elements.hintText.textContent = State.currentProfile
            ? 'No Babels for this profile'
            : 'Hold R to create your first babel';
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
        leftPanel.querySelector('.babel-edit-description').value = State.isReadOnlyProfile
            ? ProfileSelector.htmlToPlainText(left.description)
            : left.description;
        leftPanel.dataset.babelId = left.id;

        const rightPanel = document.getElementById('babel-panel-right');
        rightPanel.querySelector('.babel-sphere').style.backgroundColor = right.color;
        rightPanel.querySelector('.babel-edit-title').value = right.title;
        rightPanel.querySelector('.babel-edit-description').value = State.isReadOnlyProfile
            ? ProfileSelector.htmlToPlainText(right.description)
            : right.description;
        rightPanel.dataset.babelId = right.id;

        const readOnly = State.isReadOnlyProfile;
        comparisonOverlay.classList.toggle('read-only', readOnly);
        comparisonOverlay.querySelectorAll('.babel-edit-title, .babel-edit-description')
            .forEach((field) => { field.readOnly = readOnly; });
        document.getElementById('edge-indicator').hidden = readOnly;

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

        if (leftBabel && !State.isReadOnlyProfile) {
            leftBabel.title = leftPanel.querySelector('.babel-edit-title').value.trim() || 'Untitled';
            leftBabel.description = leftPanel.querySelector('.babel-edit-description').value.trim();
        }

        if (rightBabel && !State.isReadOnlyProfile) {
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

        const readOnly = State.isReadOnlyProfile;
        this.elements.editOverlay.classList.toggle('read-only', readOnly);
        this.elements.editTitle.readOnly = readOnly;
        this.elements.editColorPalette.hidden = readOnly;
        this.elements.editHelpBtn.hidden = readOnly;

        this.elements.editTitle.value = babel.title || '';

        if (Editor.editor) {
            if (babel.contentDelta) {
                Editor.editor.setContents(babel.contentDelta);
            } else {
                Editor.editor.root.innerHTML = babel.description || '';
            }
            Editor.editor.enable(!readOnly);
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
        if (!readOnly) setTimeout(() => this.elements.editTitle.focus(), 100);
    },

    closeEdit() {
        if (!State.isReadOnlyProfile) Editor.saveCurrentBabel();

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
    },

    async initProfileSelector() {
        this.elements.profileSelector.classList.remove('hidden');
        this.elements.graphProfileBar.hidden = true;
        await this.loadProfiles();
    },

    async loadProfiles() {
        if (this.profileRequestPending) return;
        this.profileRequestPending = true;
        const requestVersion = ++this.profileRequestVersion;
        this.retryProfileAction = () => this.loadProfiles();
        this.setProfileStatus('Connecting to the local archive...', false);
        this.elements.profileWheel.setAttribute('aria-busy', 'true');
        this.elements.profileSelectorRetry.disabled = true;

        try {
            if (!window.electronAPI?.listProfiles) {
                throw new Error('Profile loading is only available in the Electron app');
            }
            const result = await window.electronAPI.listProfiles();
            if (requestVersion !== this.profileRequestVersion) return;
            if (!result?.success) throw new Error(result?.error || 'Unable to list profiles');
            const profiles = ProfileSelector.orderedProfiles(result.data?.profiles);
            if (profiles.length !== 21) throw new Error('The backend did not return the 21-profile roster');
            this.profiles = profiles;
            this.activeProfileIndex = 0;
            this.renderProfileWheel();
            this.setProfileStatus('Choose a profile', false);
        } catch (error) {
            if (requestVersion !== this.profileRequestVersion) return;
            this.profiles = [];
            this.elements.profileWheelList.replaceChildren();
            this.setProfileStatus(error instanceof Error ? error.message : 'Unable to connect', true);
        } finally {
            if (requestVersion === this.profileRequestVersion) {
                this.profileRequestPending = false;
                this.elements.profileWheel.setAttribute('aria-busy', 'false');
                this.elements.profileSelectorRetry.disabled = false;
            }
        }
    },

    renderProfileWheel() {
        const fragment = document.createDocumentFragment();
        this.profiles.forEach((profile, index) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'profile-wheel-row';
            row.setAttribute('role', 'option');
            row.id = `profile-option-${index}`;
            row.dataset.profileIndex = String(index);
            row.style.setProperty('--profile-color', profile.color);
            row.innerHTML = `<span class="profile-wheel-order">${String(profile.order).padStart(2, '0')}</span><span class="profile-wheel-name"></span>`;
            row.querySelector('.profile-wheel-name').textContent = profile.displayName;
            row.addEventListener('mouseenter', () => this.setActiveProfile(index));
            row.addEventListener('click', () => {
                this.setActiveProfile(index);
                this.selectProfile(profile);
            });
            fragment.appendChild(row);
        });
        this.elements.profileWheelList.replaceChildren(fragment);
        this.setActiveProfile(0);
    },

    setActiveProfile(index) {
        if (this.profiles.length === 0) return;
        this.activeProfileIndex = Math.max(0, Math.min(index, this.profiles.length - 1));
        const active = this.profiles[this.activeProfileIndex];
        this.elements.profileSelector.style.setProperty('--active-profile-color', active.color);
        this.elements.profileWheelList.querySelectorAll('.profile-wheel-row').forEach((row) => {
            const rowIndex = Number(row.dataset.profileIndex);
            row.style.setProperty('--wheel-offset', rowIndex - this.activeProfileIndex);
            const selected = rowIndex === this.activeProfileIndex;
            row.classList.toggle('active', selected);
            row.setAttribute('aria-selected', String(selected));
            row.tabIndex = selected ? 0 : -1;
        });
        const activeRow = this.elements.profileWheelList.querySelector('.profile-wheel-row.active');
        this.elements.profileWheel.setAttribute('aria-activedescendant', activeRow?.id || '');
    },

    moveActiveProfile(direction) {
        this.setActiveProfile(this.activeProfileIndex + direction);
        this.elements.profileWheelList.querySelector('.profile-wheel-row.active')?.focus();
    },

    setProfileStatus(message, showRetry) {
        this.elements.profileSelectorStatus.textContent = message;
        this.elements.profileSelectorStatus.classList.toggle('error', showRetry);
        this.elements.profileSelectorRetry.hidden = !showRetry;
    },

    async selectProfile(profile) {
        if (this.profileRequestPending) return;
        this.profileRequestPending = true;
        const requestVersion = ++this.profileRequestVersion;
        this.retryProfileAction = () => this.selectProfile(profile);
        this.setProfileStatus(`Opening ${profile.displayName}...`, false);
        this.elements.profileWheel.setAttribute('aria-busy', 'true');
        this.elements.profileSelectorRetry.disabled = true;

        try {
            const result = await window.electronAPI.loadProfileGraph(profile.id);
            if (requestVersion !== this.profileRequestVersion) return;
            if (!result?.success) throw new Error(result?.error || 'Unable to load profile');
            if (result.data?.profile?.id !== profile.id) {
                throw new Error('The backend returned a different profile graph');
            }
            ProfileSelector.applyProfileGraph(State, result.data);
            this.elements.graphProfileName.textContent = State.currentProfile.displayName;
            this.elements.graphProfileName.style.color = State.currentProfile.color;
            this.elements.graphProfileBar.hidden = false;
            this.elements.profileSelector.classList.add('hidden');
            updateGraph();
            this.updateHintText();
        } catch (error) {
            if (requestVersion !== this.profileRequestVersion) return;
            State.babels = [];
            State.edges = [];
            State.currentProfile = null;
            State.isReadOnlyProfile = false;
            ProfileSelector.clearTransientState(State);
            this.setProfileStatus(error instanceof Error ? error.message : 'Unable to load profile', true);
        } finally {
            if (requestVersion === this.profileRequestVersion) {
                this.profileRequestPending = false;
                this.elements.profileWheel.setAttribute('aria-busy', 'false');
                this.elements.profileSelectorRetry.disabled = false;
            }
        }
    },

    showProfileSelector() {
        ++this.profileRequestVersion;
        this.profileRequestPending = false;
        if (Editor.autoSaveTimer) clearTimeout(Editor.autoSaveTimer);
        Editor.autoSaveTimer = null;
        Editor.editor?.enable(false);
        this.cleanupPreview();
        this.elements.comparisonOverlay.classList.remove('active');
        this.elements.editOverlay.classList.remove('active');
        this.elements.creationForm.classList.remove('active');
        this.elements.creationOverlay.classList.remove('active');
        ProfileSelector.clearTransientState(State);
        State.babels = [];
        State.edges = [];
        State.currentProfile = null;
        State.isReadOnlyProfile = false;
        this.elements.graphProfileBar.hidden = true;
        this.elements.profileSelector.classList.remove('hidden');
        this.setProfileStatus('Choose a profile', false);
        updateGraph();
        this.updateHintText();
        this.elements.profileSelector.focus();
    }
};
