// ============================================
// UI HANDLERS
// ============================================

const UI = {
    // DOM element references (set during init)
    elements: {},

    // Quill editor instance
    editor: null,

    // Auto-save timer
    autoSaveTimer: null,

    // Current heading level for cycling (0 = normal, 1-3 = h1-h3)
    currentHeadingLevel: 0,

    // 3D Preview state
    preview: {
        scene: null,
        camera: null,
        renderer: null,
        sphere: null,
        innerSphere: null,
        texture: null,
        animationId: null
    },

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
            // New edit mode elements
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

        // Creation palette
        if (colorPalette) {
            colorPalette.innerHTML = '';
            Config.colors.bright.forEach((color, index) => {
                const swatch = this.createColorSwatch(color, index === 0, colorPalette, 'creation');
                colorPalette.appendChild(swatch);
            });
        }

        // Edit palette
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
            // Update 3D preview color in edit mode
            if (mode === 'edit') {
                this.updatePreviewColor(color);
                this.triggerAutoSave();
            }
        });
        return swatch;
    },

    /**
     * Initialize 3D babel preview with wedge cutout
     * - Wedge stays fixed facing camera
     * - Texture rotates to simulate planet rotation
     * - Visible interior (not black void)
     */
    initPreview(color) {
        const container = this.elements.editBabel3d;
        if (!container) return;

        // Clean up existing preview
        this.cleanupPreview();

        const width = container.clientWidth || 400;
        const height = container.clientHeight || 400;

        // Create scene
        this.preview.scene = new THREE.Scene();
        this.preview.scene.background = new THREE.Color(0x0a0a0a);

        // Create camera
        this.preview.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
        this.preview.camera.position.z = 10;

        // Create renderer
        this.preview.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.preview.renderer.setSize(width, height);
        this.preview.renderer.setPixelRatio(window.devicePixelRatio);
        container.innerHTML = '';
        container.appendChild(this.preview.renderer.domElement);

        // Create outer sphere with wedge cutout - geometry stays FIXED
        const outerGeometry = this.createWedgeSphereGeometry(4, 64, Math.PI * 0.35);
        const texture = Rendering.createPlanetTexture(color);
        texture.wrapS = THREE.RepeatWrapping;
        texture.wrapT = THREE.RepeatWrapping;
        const outerMaterial = new THREE.MeshStandardMaterial({
            map: texture,
            side: THREE.DoubleSide
        });
        this.preview.sphere = new THREE.Mesh(outerGeometry, outerMaterial);
        this.preview.scene.add(this.preview.sphere);

        // Create inner sphere for visible hollow interior (dark grey, not black)
        const innerGeometry = new THREE.SphereGeometry(3.85, 64, 64);
        const innerMaterial = new THREE.MeshStandardMaterial({
            color: 0x1a1a1a,
            side: THREE.BackSide,
            roughness: 0.9
        });
        this.preview.innerSphere = new THREE.Mesh(innerGeometry, innerMaterial);
        this.preview.scene.add(this.preview.innerSphere);

        // Add lighting - brighter to illuminate interior
        const ambientLight = new THREE.AmbientLight(0x606060, 1.0);
        this.preview.scene.add(ambientLight);

        const mainLight = new THREE.DirectionalLight(0xffffff, 1.0);
        mainLight.position.set(5, 5, 5);
        this.preview.scene.add(mainLight);

        const fillLight = new THREE.DirectionalLight(0x6688cc, 0.5);
        fillLight.position.set(-5, -3, -5);
        this.preview.scene.add(fillLight);

        // Interior point light to illuminate the hollow
        const interiorLight = new THREE.PointLight(0x444444, 0.8, 10);
        interiorLight.position.set(0, 0, 0);
        this.preview.scene.add(interiorLight);

        // Store texture reference for rotation animation
        this.preview.texture = texture;

        // Start animation loop
        this.animatePreview();
    },

    /**
     * Create sphere geometry with a wedge (pie slice) removed
     */
    createWedgeSphereGeometry(radius, segments, wedgeAngle) {
        const geometry = new THREE.SphereGeometry(radius, segments, segments);
        const positions = geometry.attributes.position;
        const indices = [];
        const originalIndex = geometry.index.array;

        // Keep faces that aren't in the wedge
        for (let i = 0; i < originalIndex.length; i += 3) {
            const idx1 = originalIndex[i];
            const idx2 = originalIndex[i + 1];
            const idx3 = originalIndex[i + 2];

            // Check if any vertex of the face is in the wedge
            let inWedge = false;
            for (const idx of [idx1, idx2, idx3]) {
                const x = positions.getX(idx);
                const z = positions.getZ(idx);
                const angle = Math.atan2(z, x);

                // Wedge is centered around positive X axis
                if (angle > -wedgeAngle / 2 && angle < wedgeAngle / 2 && x > 0) {
                    inWedge = true;
                    break;
                }
            }

            if (!inWedge) {
                indices.push(idx1, idx2, idx3);
            }
        }

        geometry.setIndex(indices);
        return geometry;
    },

    /**
     * Update preview sphere color
     */
    updatePreviewColor(color) {
        if (!this.preview.sphere) return;

        // Create new texture with new color, preserve rotation settings
        const texture = Rendering.createPlanetTexture(color);
        texture.wrapS = THREE.RepeatWrapping;
        texture.wrapT = THREE.RepeatWrapping;
        // Copy current offset from old texture
        if (this.preview.texture) {
            texture.offset.copy(this.preview.texture.offset);
        }
        this.preview.sphere.material.map = texture;
        this.preview.sphere.material.needsUpdate = true;
        this.preview.texture = texture;
    },

    /**
     * Animate the preview (texture rotation - geometry stays fixed)
     */
    animatePreview() {
        if (!this.preview.renderer || !this.preview.scene || !this.preview.camera) return;

        const animate = () => {
            this.preview.animationId = requestAnimationFrame(animate);

            // Rotate texture offset instead of geometry (wedge stays facing camera)
            if (this.preview.texture) {
                this.preview.texture.offset.x += 0.001;
            }

            this.preview.renderer.render(this.preview.scene, this.preview.camera);
        };

        animate();
    },

    /**
     * Clean up 3D preview
     */
    cleanupPreview() {
        if (this.preview.animationId) {
            cancelAnimationFrame(this.preview.animationId);
            this.preview.animationId = null;
        }

        if (this.preview.renderer) {
            this.preview.renderer.dispose();
            this.preview.renderer = null;
        }

        if (this.preview.sphere) {
            this.preview.sphere.geometry.dispose();
            this.preview.sphere.material.dispose();
            this.preview.sphere = null;
        }

        if (this.preview.innerSphere) {
            this.preview.innerSphere.geometry.dispose();
            this.preview.innerSphere.material.dispose();
            this.preview.innerSphere = null;
        }

        if (this.preview.texture) {
            this.preview.texture.dispose();
            this.preview.texture = null;
        }

        this.preview.scene = null;
        this.preview.camera = null;
    },

    // Track if Quill blots have been registered
    blotsRegistered: false,

    /**
     * Initialize Quill editor with custom keyboard shortcuts
     */
    initEditor() {
        if (this.editor) {
            return; // Already initialized
        }

        const Inline = Quill.import('blots/inline');
        const Embed = Quill.import('blots/embed');

        // Only register blots once
        if (this.blotsRegistered) {
            this.createQuillInstance();
            return;
        }
        this.blotsRegistered = true;

        // Register custom highlight format
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

        // YouTube embed blot - inline pill
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

        // PDF embed blot - inline pill
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
                    if (value.url) {
                        window.open(value.url, '_blank');
                    }
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

        // Create the Quill instance
        this.createQuillInstance();
    },

    /**
     * Create Quill instance and set up event handlers
     */
    createQuillInstance() {
        this.editor = new Quill('#edit-editor', {
            theme: 'snow',
            placeholder: 'Start writing...',
            modules: {
                toolbar: false, // No toolbar - keyboard shortcuts only
                keyboard: {
                    bindings: {
                        // Override default behaviors
                    }
                }
            }
        });

        // Custom keyboard shortcuts
        this.setupEditorShortcuts();

        // Auto-save on content change
        this.editor.on('text-change', () => {
            this.triggerAutoSave();
        });

        // Handle paste for YouTube/PDF detection
        this.editor.root.addEventListener('paste', (e) => {
            this.handlePaste(e);
        });

        // Handle @ for babel linking
        this.editor.on('text-change', (delta, oldDelta, source) => {
            if (source === 'user') {
                this.checkForBabelMention();
            }
        });
    },

    /**
     * Setup custom keyboard shortcuts for the editor
     */
    setupEditorShortcuts() {
        const editor = this.editor;

        // Helper to apply format to selection
        const toggleFormat = (format, value = true) => {
            const range = editor.getSelection();
            if (range) {
                const currentFormat = editor.getFormat(range);
                editor.format(format, !currentFormat[format]);
            }
        };

        // Listen for keyboard events on the editor
        editor.root.addEventListener('keydown', (e) => {
            // Ctrl+B - Bold
            if (e.ctrlKey && e.key === 'b') {
                e.preventDefault();
                toggleFormat('bold');
            }
            // Ctrl+I - Italic
            else if (e.ctrlKey && e.key === 'i') {
                e.preventDefault();
                toggleFormat('italic');
            }
            // Ctrl+Shift+S - Strikethrough
            else if (e.ctrlKey && e.shiftKey && e.key === 'S') {
                e.preventDefault();
                toggleFormat('strike');
            }
            // Ctrl+H - Highlight
            else if (e.ctrlKey && e.key === 'h') {
                e.preventDefault();
                toggleFormat('highlight');
            }
            // Ctrl+Shift+L - Bullet list
            else if (e.ctrlKey && e.shiftKey && e.key === 'L') {
                e.preventDefault();
                toggleFormat('list', 'bullet');
            }
            // Ctrl+Shift+C - Code block
            else if (e.ctrlKey && e.shiftKey && e.key === 'C') {
                e.preventDefault();
                toggleFormat('code-block');
            }
            // Ctrl++ (Ctrl+=) - Heading up (increase)
            else if (e.ctrlKey && (e.key === '=' || e.key === '+')) {
                e.preventDefault();
                this.cycleHeading(1);
            }
            // Ctrl+- - Heading down (decrease)
            else if (e.ctrlKey && e.key === '-') {
                e.preventDefault();
                this.cycleHeading(-1);
            }
        });
    },

    /**
     * Cycle heading level: normal -> h1 -> h2 -> h3 -> normal (wraps)
     */
    cycleHeading(direction) {
        const range = this.editor.getSelection();
        if (!range) return;

        const format = this.editor.getFormat(range);
        let currentLevel = 0;
        if (format.header) {
            currentLevel = format.header;
        }

        // Cycle: 0 -> 1 -> 2 -> 3 -> 0 (direction = 1)
        // Or: 0 -> 3 -> 2 -> 1 -> 0 (direction = -1)
        let newLevel = currentLevel + direction;
        if (newLevel > 3) newLevel = 0;
        if (newLevel < 0) newLevel = 3;

        this.editor.format('header', newLevel === 0 ? false : newLevel);
    },

    /**
     * Handle paste events for YouTube/PDF auto-detection
     */
    handlePaste(e) {
        // Check for files (PDF)
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

        // Check for YouTube URL in text
        const text = e.clipboardData?.getData('text/plain');
        if (text && this.isYouTubeURL(text)) {
            e.preventDefault();
            this.embedYouTube(text);
        }
    },

    /**
     * Check if URL is a YouTube URL
     */
    isYouTubeURL(url) {
        const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)[\w-]+/;
        return youtubeRegex.test(url);
    },

    /**
     * Extract YouTube video ID from URL
     */
    getYouTubeID(url) {
        const match = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([\w-]+)/);
        return match ? match[1] : null;
    },

    /**
     * Embed YouTube video as inline pill
     */
    embedYouTube(url) {
        const videoId = this.getYouTubeID(url);
        if (!videoId) return;

        // Insert using Quill's embed API (click handler is in the blot)
        const range = this.editor.getSelection(true);
        this.editor.insertEmbed(range.index, 'youtube', { url }, 'user');
        this.editor.setSelection(range.index + 1);
    },

    /**
     * Embed PDF file as inline pill
     * TODO: Later - store in IndexedDB with device ID
     */
    embedPDF(file) {
        // Create object URL for the file (temporary, for this session)
        const fileUrl = URL.createObjectURL(file);

        // Insert using Quill's embed API (click handler is in the blot)
        const range = this.editor.getSelection(true);
        this.editor.insertEmbed(range.index, 'pdf', { url: fileUrl, filename: file.name }, 'user');
        this.editor.setSelection(range.index + 1);
    },

    /**
     * Check for @ babel mention
     * TODO: Later - implement recommendations for neighbor/nested babels
     */
    checkForBabelMention() {
        const range = this.editor.getSelection();
        if (!range) return;

        const text = this.editor.getText();
        const cursorPos = range.index;

        // Find @ before cursor
        let atPos = -1;
        for (let i = cursorPos - 1; i >= 0; i--) {
            if (text[i] === '@') {
                atPos = i;
                break;
            }
            if (text[i] === ' ' || text[i] === '\n') {
                break;
            }
        }

        if (atPos >= 0) {
            // Show babel selector popup
            // For now: just show "No babels found" as per requirements
            // TODO: Later - implement getRecommendedBabels() for neighbor/nested babels
            this.showBabelSelector(atPos);
        }
    },

    /**
     * Show babel selector popup
     * Returns empty for now - will implement neighbor/nested babel recommendations later
     */
    showBabelSelector(atPosition) {
        // TODO: Implement popup UI
        // For now, the recommendation function returns empty array
        const recommendations = this.getRecommendedBabels();

        if (recommendations.length === 0) {
            // Show "No babels found" briefly
            // This would be a popup near the cursor
            console.log('No babels found'); // Placeholder
        }
    },

    /**
     * Get recommended babels for @ linking
     * TODO: Later - return neighbor babels or babels inside current babel
     * For now, returns empty array
     */
    getRecommendedBabels() {
        // Future: return babels that are neighbors (connected) or nested inside current babel
        return [];
    },

    /**
     * Trigger auto-save with debounce (silent - no indicator)
     */
    triggerAutoSave() {
        if (this.autoSaveTimer) {
            clearTimeout(this.autoSaveTimer);
        }

        this.autoSaveTimer = setTimeout(() => {
            this.saveCurrentBabel();
        }, 2000); // 2 second debounce
    },

    /**
     * Save current babel data
     */
    saveCurrentBabel() {
        const babel = State.editingBabel;
        if (!babel) return;

        babel.title = this.elements.editTitle.value.trim() || 'Untitled';
        babel.description = this.editor ? this.editor.root.innerHTML : '';
        babel.color = State.selectedColor;

        Persistence.save();
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

        // Help button toggle
        const helpBtn = this.elements.editHelpBtn;
        const shortcutsPopup = this.elements.editShortcutsPopup;
        if (helpBtn && shortcutsPopup) {
            helpBtn.addEventListener('click', () => {
                shortcutsPopup.classList.toggle('visible');
            });
            // Close popup when clicking outside
            document.addEventListener('click', (e) => {
                if (!helpBtn.contains(e.target) && !shortcutsPopup.contains(e.target)) {
                    shortcutsPopup.classList.remove('visible');
                }
            });
        }

        // Title auto-save
        const editTitle = this.elements.editTitle;
        if (editTitle) {
            editTitle.addEventListener('input', () => {
                this.triggerAutoSave();
            });
        }
    },

    handleKeyDown(e, callbacks) {
        const { comparisonOverlay, editOverlay, creationForm } = this.elements;

        // Allow typing in editor
        if (this.hasClass(editOverlay, 'active')) {
            // In edit mode, only handle Escape
            if (e.key === 'Escape') {
                callbacks.closeEdit();
            }
            return;
        }

        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            if (e.key === 'Escape') e.target.blur();
            if (e.key === 'Enter' && this.hasClass(creationForm, 'active')) {
                e.preventDefault();
                callbacks.createBabel();
            }
            return;
        }

        switch (e.key.toLowerCase()) {
            case 'r':
                if (!State.isCreating &&
                    !this.hasClass(comparisonOverlay, 'active') &&
                    !this.hasClass(editOverlay, 'active')) {
                    callbacks.startCreation();
                }
                break;
            case 'escape':
                if (this.hasClass(creationForm, 'active')) {
                    callbacks.cancelCreation();
                } else if (this.hasClass(comparisonOverlay, 'active')) {
                    callbacks.closeComparison();
                } else if (this.hasClass(editOverlay, 'active')) {
                    callbacks.closeEdit();
                } else if (State.selectedBabel) {
                    callbacks.deselectBabel();
                }
                break;
            case 'delete':
            case 'backspace':
                if (State.selectedBabel &&
                    !this.hasClass(comparisonOverlay, 'active') &&
                    !this.hasClass(editOverlay, 'active')) {
                    callbacks.handleDelete();
                }
                break;
            case 'e':
                if (State.selectedBabel &&
                    !this.hasClass(comparisonOverlay, 'active') &&
                    !this.hasClass(editOverlay, 'active')) {
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
     * Edit mode - New split layout
     */
    openEdit(babel) {
        State.editingBabel = babel;
        State.selectedColor = babel.color;

        // Initialize editor if not already done
        this.initEditor();

        // Set title
        this.elements.editTitle.value = babel.title || '';

        // Set editor content
        if (this.editor) {
            this.editor.root.innerHTML = babel.description || '';
        }

        // Setup color palette and select current color
        this.setupColorPalette();
        const colorPalette = this.elements.editColorPalette;
        if (colorPalette) {
            colorPalette.querySelectorAll('.color-swatch').forEach((swatch) => {
                swatch.classList.toggle('selected', swatch.style.backgroundColor === babel.color);
            });
        }

        // Show edit overlay
        this.elements.editOverlay.classList.add('active');

        // Initialize 3D preview after overlay is visible
        setTimeout(() => {
            this.initPreview(babel.color);
        }, 50);

        // Focus title after a brief delay
        setTimeout(() => {
            this.elements.editTitle.focus();
        }, 100);
    },

    closeEdit(callback) {
        // Save before closing
        this.saveCurrentBabel();

        // Clear auto-save timer
        if (this.autoSaveTimer) {
            clearTimeout(this.autoSaveTimer);
            this.autoSaveTimer = null;
        }

        // Clean up 3D preview
        this.cleanupPreview();

        // Close shortcuts popup if open
        if (this.elements.editShortcutsPopup) {
            this.elements.editShortcutsPopup.classList.remove('visible');
        }

        // Hide overlay
        this.elements.editOverlay.classList.remove('active');

        // Clear texture cache so main graph shows updated colors
        Rendering.textureCache.clear();

        State.editingBabel = null;

        if (callback) callback();
    }
};
