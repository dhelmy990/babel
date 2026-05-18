// ============================================
// BABEL - Knowledge Graph System
// ============================================

// Generate Neptune-like procedural texture
function createPlanetTexture(baseColor, size = 256) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');

    // Parse the base color
    const color = new THREE.Color(baseColor);
    const hsl = {};
    color.getHSL(hsl);

    // Fill with base color
    ctx.fillStyle = baseColor;
    ctx.fillRect(0, 0, size, size);

    // Add murky bands (like gas giant atmosphere)
    for (let i = 0; i < 12; i++) {
        const y = (i / 12) * size;
        const bandHeight = size / 12 + Math.random() * 10;

        // Vary the lightness for each band
        const bandLightness = hsl.l + (Math.random() - 0.5) * 0.3;
        const bandColor = new THREE.Color().setHSL(hsl.h, hsl.s * 0.8, Math.max(0.1, Math.min(0.9, bandLightness)));

        ctx.fillStyle = '#' + bandColor.getHexString();
        ctx.globalAlpha = 0.3 + Math.random() * 0.4;
        ctx.fillRect(0, y, size, bandHeight);
    }

    // Add swirling noise/turbulence
    ctx.globalAlpha = 0.15;
    for (let i = 0; i < 200; i++) {
        const x = Math.random() * size;
        const y = Math.random() * size;
        const radius = Math.random() * 20 + 5;

        const noiseColor = new THREE.Color().setHSL(
            hsl.h + (Math.random() - 0.5) * 0.1,
            hsl.s,
            hsl.l + (Math.random() - 0.5) * 0.4
        );

        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fillStyle = '#' + noiseColor.getHexString();
        ctx.fill();
    }

    // Add some darker storm spots
    ctx.globalAlpha = 0.25;
    for (let i = 0; i < 5; i++) {
        const x = Math.random() * size;
        const y = Math.random() * size;
        const radiusX = Math.random() * 30 + 10;
        const radiusY = Math.random() * 15 + 5;

        ctx.beginPath();
        ctx.ellipse(x, y, radiusX, radiusY, Math.random() * Math.PI, 0, Math.PI * 2);
        ctx.fillStyle = new THREE.Color().setHSL(hsl.h, hsl.s, hsl.l * 0.5).getStyle();
        ctx.fill();
    }

    ctx.globalAlpha = 1;

    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;
    return texture;
}

// Cache for planet textures
const textureCache = new Map();

function getPlanetTexture(color) {
    if (!textureCache.has(color)) {
        textureCache.set(color, createPlanetTexture(color));
    }
    return textureCache.get(color);
}

// Color presets (bright for babels, dark for animation)
const COLOR_PRESETS = {
    bright_actual: [
        '#FF6B6B', // Red
        '#4ECDC4', // Teal
        '#45B7D1', // Sky blue
        '#96CEB4', // Sage
        '#FFEAA7', // Yellow
        '#DDA0DD', // Plum
        '#98D8C8', // Mint
        '#F7DC6F', // Gold
        '#BB8FCE', // Lavender
        '#85C1E9'  // Light blue
    ],
    bright: [
        '#8B0000', // Dark red
        '#006666', // Dark teal
        '#1a5276', // Dark blue
        '#1e8449', // Dark green
        '#7d6608', // Dark yellow
        '#6c3483', // Dark purple
        '#117a65', // Dark mint
        '#7e5109', // Dark gold
        '#4a235a', // Dark lavender
        '#1b4f72'  // Dark sky
    ],
    dark: [
        '#8B0000', // Dark red
        '#006666', // Dark teal
        '#1a5276', // Dark blue
        '#1e8449', // Dark green
        '#7d6608', // Dark yellow
        '#6c3483', // Dark purple
        '#117a65', // Dark mint
        '#7e5109', // Dark gold
        '#4a235a', // Dark lavender
        '#1b4f72'  // Dark sky
    ]
};

// ============================================
// GRAPH UTILITIES (DAG, Transitivity, Cycles)
// ============================================

// Get only dependency edges (not associations - those are bidirectional)
function getDependencyEdges() {
    const edgePairs = new Map();

    // Group edges by node pairs
    state.edges.forEach(edge => {
        const key = [edge.source, edge.target].sort().join('-');
        if (!edgePairs.has(key)) {
            edgePairs.set(key, []);
        }
        edgePairs.get(key).push(edge);
    });

    // Filter to only single-direction edges (dependencies, not associations)
    const dependencies = [];
    edgePairs.forEach((edges, key) => {
        if (edges.length === 1) {
            dependencies.push(edges[0]);
        }
        // If edges.length === 2, it's an association (bidirectional) - skip for transitivity
    });

    return dependencies;
}

// Check if there's a path from source to target using only dependency edges
// (Used for transitivity detection)
function hasPath(sourceId, targetId, excludeDirectEdge = false) {
    const dependencies = getDependencyEdges();
    const visited = new Set();
    const queue = [sourceId];

    // Build adjacency list (source -> [targets])
    const adj = new Map();
    dependencies.forEach(edge => {
        if (excludeDirectEdge && edge.source === sourceId && edge.target === targetId) {
            return; // Skip the direct edge we're checking
        }
        if (!adj.has(edge.source)) {
            adj.set(edge.source, []);
        }
        adj.get(edge.source).push(edge.target);
    });

    while (queue.length > 0) {
        const current = queue.shift();
        if (current === targetId) {
            return true;
        }
        if (visited.has(current)) {
            continue;
        }
        visited.add(current);

        const neighbors = adj.get(current) || [];
        for (const neighbor of neighbors) {
            if (!visited.has(neighbor)) {
                queue.push(neighbor);
            }
        }
    }

    return false;
}

// Check if adding edge source->target would create a cycle
function wouldCreateCycle(sourceId, targetId) {
    // A cycle would be created if target can already reach source
    // (adding source->target would complete the cycle)
    const dependencies = getDependencyEdges();
    const visited = new Set();
    const queue = [targetId];

    // Build adjacency list
    const adj = new Map();
    dependencies.forEach(edge => {
        if (!adj.has(edge.source)) {
            adj.set(edge.source, []);
        }
        adj.get(edge.source).push(edge.target);
    });

    while (queue.length > 0) {
        const current = queue.shift();
        if (current === sourceId) {
            return true; // Target can reach source, so adding source->target creates cycle
        }
        if (visited.has(current)) {
            continue;
        }
        visited.add(current);

        const neighbors = adj.get(current) || [];
        for (const neighbor of neighbors) {
            if (!visited.has(neighbor)) {
                queue.push(neighbor);
            }
        }
    }

    return false;
}

// Find and remove transitive edges
// An edge A->C is transitive if there exists a path A->...->C without using A->C directly
function pruneTransitiveEdges() {
    const dependencies = getDependencyEdges();
    const toRemove = [];

    dependencies.forEach(edge => {
        // Check if there's an alternative path from source to target
        if (hasPath(edge.source, edge.target, true)) {
            toRemove.push(edge);
        }
    });

    // Remove transitive edges
    toRemove.forEach(edge => {
        const index = state.edges.findIndex(e => e.source === edge.source && e.target === edge.target);
        if (index >= 0) {
            console.log(`Pruning transitive edge: ${edge.source} -> ${edge.target}`);
            state.edges.splice(index, 1);
        }
    });

    return toRemove.length;
}

// Detect and break cycles in the graph
function breakCycles() {
    let cyclesBroken = 0;
    let hasChanges = true;

    while (hasChanges) {
        hasChanges = false;
        const dependencies = getDependencyEdges();

        // Try to find a cycle using DFS
        const visited = new Set();
        const recursionStack = new Set();

        for (const startEdge of dependencies) {
            const cycleEdge = findCycleEdge(startEdge.source, visited, recursionStack, dependencies);
            if (cycleEdge) {
                // Remove the edge that completes the cycle
                const index = state.edges.findIndex(e => e.source === cycleEdge.source && e.target === cycleEdge.target);
                if (index >= 0) {
                    console.log(`Breaking cycle by removing edge: ${cycleEdge.source} -> ${cycleEdge.target}`);
                    state.edges.splice(index, 1);
                    cyclesBroken++;
                    hasChanges = true;
                    break;
                }
            }
        }
    }

    return cyclesBroken;
}

function findCycleEdge(nodeId, visited, recursionStack, dependencies, path = []) {
    if (recursionStack.has(nodeId)) {
        // Found a cycle - return the last edge in the path
        return path[path.length - 1];
    }
    if (visited.has(nodeId)) {
        return null;
    }

    visited.add(nodeId);
    recursionStack.add(nodeId);

    // Build adjacency
    const adj = new Map();
    dependencies.forEach(edge => {
        if (!adj.has(edge.source)) {
            adj.set(edge.source, []);
        }
        adj.get(edge.source).push({ target: edge.target, edge });
    });

    const neighbors = adj.get(nodeId) || [];
    for (const { target, edge } of neighbors) {
        path.push(edge);
        const result = findCycleEdge(target, visited, recursionStack, dependencies, path);
        if (result) {
            return result;
        }
        path.pop();
    }

    recursionStack.delete(nodeId);
    return null;
}

// Clean graph on load: break cycles, then prune transitive edges
function cleanGraphOnLoad() {
    const cyclesBroken = breakCycles();
    const transitivePruned = pruneTransitiveEdges();

    if (cyclesBroken > 0 || transitivePruned > 0) {
        console.log(`Graph cleaned: ${cyclesBroken} cycles broken, ${transitivePruned} transitive edges pruned`);
    }
}

// App state
const state = {
    babels: [],
    edges: [],
    selectedBabel: null,
    comparisonBabels: [],
    isCreating: false,
    creationStartTime: null,
    creationAnimationFrame: null,
    currentColorIndex: 0,
    selectedColor: COLOR_PRESETS.bright[0],
    selectedSimilarBabels: [],
    deleteWarningBabel: null,
    deleteWarningTimeout: null,
    isPhysicsPaused: false,
    editingBabel: null
};

// DOM elements
const graphContainer = document.getElementById('graph-container');
const hintText = document.getElementById('hint-text');
const creationOverlay = document.getElementById('creation-overlay');
const creationCircle = document.getElementById('creation-circle');
const creationForm = document.getElementById('creation-form');
const colorPalette = document.getElementById('color-palette');
const similarBabels = document.getElementById('similar-babels');
const comparisonOverlay = document.getElementById('comparison-overlay');
const editOverlay = document.getElementById('edit-overlay');
const saveToast = document.getElementById('save-toast');

// Initialize ForceGraph3D
const Graph = ForceGraph3D()(graphContainer)
    .backgroundColor('#0a0a0a')
    .nodeThreeObject(node => {
        const group = new THREE.Group();

        // Determine color based on state
        let color = node.color || '#1a5276';
        if (state.deleteWarningBabel && state.deleteWarningBabel.id === node.id) {
            color = '#ff0000';
        }

        // Main sphere with planetary texture
        const geometry = new THREE.SphereGeometry(5, 64, 64);
        const texture = getPlanetTexture(color);

        const material = new THREE.MeshStandardMaterial({
            map: texture,
            roughness: 0.8,
            metalness: 0.1,
            emissive: new THREE.Color(color),
            emissiveIntensity: 0.15
        });

        const sphere = new THREE.Mesh(geometry, material);

        // Slight axial tilt for visual interest
        sphere.rotation.z = Math.random() * 0.3;

        group.add(sphere);

        // Add atmosphere glow
        const glowGeometry = new THREE.SphereGeometry(5.5, 32, 32);
        const glowMaterial = new THREE.MeshBasicMaterial({
            color: color,
            transparent: true,
            opacity: 0.15,
            side: THREE.BackSide
        });
        const glow = new THREE.Mesh(glowGeometry, glowMaterial);
        group.add(glow);

        // Selection ring if selected
        if (state.selectedBabel && state.selectedBabel.id === node.id) {
            const ringGeometry = new THREE.TorusGeometry(7, 0.4, 16, 100);
            const ringMaterial = new THREE.MeshBasicMaterial({
                color: '#ffffff',
                transparent: true,
                opacity: 0.8
            });
            const ring = new THREE.Mesh(ringGeometry, ringMaterial);
            ring.rotation.x = Math.PI / 2;
            group.add(ring);
        }

        return group;
    })
    .nodeLabel(node => node.title || 'Untitled')
    .linkWidth(link => link.isMutual ? 3 : 1)
    .linkColor(link => link.isMutual ? '#ffffff' : '#666666')
    .linkDirectionalParticles(link => link.isMutual ? 0 : 4)
    .linkDirectionalParticleSpeed(0.005)
    .linkDirectionalParticleWidth(2)
    .onNodeClick(handleNodeClick)
    .onNodeRightClick(handleNodeRightClick)
    .onBackgroundClick(handleBackgroundClick)
    .onBackgroundRightClick(() => {
        // Don't clear comparison babels on background right-click
        // This allows camera panning without losing selection
        console.log('Background right-clicked - preserving comparison state');
    })
    .enableNodeDrag(true)
    .onNodeDragEnd(node => {
        // Release node back to physics
        node.fx = undefined;
        node.fy = undefined;
        node.fz = undefined;
    })
    .dagMode('bu')
    .dagLevelDistance(20)
    .linkDirectionalArrowLength(link => link.isMutual ? 0 : 3.5)
    .linkDirectionalArrowRelPos(1)
    .onDagError(() => {
        // DAG layout failed (likely due to cycles), fall back gracefully
        console.warn('DAG layout error - graph may have cycles');
    });

// ============================================
// INITIALIZATION
// ============================================

function init() {
    setupLighting();
    loadFromLocalStorage();
    cleanGraphOnLoad(); // Break cycles and prune transitive edges
    setupColorPalette();
    setupEventListeners();
    updateGraph();
    updateHintText();
}

function setupLighting() {
    const scene = Graph.scene();

    // Remove default lights if any
    scene.children = scene.children.filter(child => !(child instanceof THREE.Light));

    // Add ambient light for base illumination
    const ambientLight = new THREE.AmbientLight(0x404040, 0.5);
    scene.add(ambientLight);

    // Add main directional light (sun-like)
    const mainLight = new THREE.DirectionalLight(0xffffff, 1.0);
    mainLight.position.set(100, 100, 100);
    scene.add(mainLight);

    // Add secondary fill light from opposite side
    const fillLight = new THREE.DirectionalLight(0x6688cc, 0.4);
    fillLight.position.set(-100, -50, -100);
    scene.add(fillLight);

    // Add point light that follows camera for rim lighting effect
    const pointLight = new THREE.PointLight(0xffffff, 0.3, 500);
    pointLight.position.set(0, 0, 200);
    scene.add(pointLight);
}

function setupColorPalette() {
    // Creation form palette
    colorPalette.innerHTML = '';
    COLOR_PRESETS.bright.forEach((color, index) => {
        const swatch = document.createElement('div');
        swatch.className = 'color-swatch' + (index === 0 ? ' selected' : '');
        swatch.style.backgroundColor = color;
        swatch.addEventListener('click', () => selectColor(index, colorPalette));
        colorPalette.appendChild(swatch);
    });

    // Edit form palette
    const editPalette = document.getElementById('edit-color-palette');
    editPalette.innerHTML = '';
    COLOR_PRESETS.bright.forEach((color, index) => {
        const swatch = document.createElement('div');
        swatch.className = 'color-swatch';
        swatch.style.backgroundColor = color;
        swatch.addEventListener('click', () => selectColor(index, editPalette));
        editPalette.appendChild(swatch);
    });
}

function selectColor(index, palette) {
    state.selectedColor = COLOR_PRESETS.bright[index];
    palette.querySelectorAll('.color-swatch').forEach((s, i) => {
        s.classList.toggle('selected', i === index);
    });
}

function setupEventListeners() {
    // R key for creation
    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener('keyup', handleKeyUp);

    // Form buttons
    document.getElementById('create-babel-btn').addEventListener('click', createBabel);
    document.getElementById('cancel-create-btn').addEventListener('click', cancelCreation);

    // Comparison mode
    document.getElementById('comparison-done-btn').addEventListener('click', closeComparison);
    document.querySelector('.edge-arrow.left-to-right').addEventListener('click', () => toggleEdge('left-to-right'));
    document.querySelector('.edge-arrow.right-to-left').addEventListener('click', () => toggleEdge('right-to-left'));

    // Edit mode
    document.getElementById('edit-done-btn').addEventListener('click', closeEdit);

    // Prevent default on Ctrl+S
    document.addEventListener('keydown', e => {
        if (e.ctrlKey && e.key === 's') {
            e.preventDefault();
            saveToLocalStorage();
        }
    });
}

// ============================================
// KEYBOARD HANDLING
// ============================================

function handleKeyDown(e) {
    // Don't handle if typing in input
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        if (e.key === 'Escape') {
            e.target.blur();
        }
        if (e.key === 'Enter' && creationForm.classList.contains('active')) {
            e.preventDefault();
            createBabel();
        }
        return;
    }

    switch(e.key.toLowerCase()) {
        case 'r':
            if (!state.isCreating && !comparisonOverlay.classList.contains('active') && !editOverlay.classList.contains('active')) {
                startCreation();
            }
            break;
        case 'escape':
            if (creationForm.classList.contains('active')) {
                cancelCreation();
            } else if (comparisonOverlay.classList.contains('active')) {
                closeComparison();
            } else if (editOverlay.classList.contains('active')) {
                closeEdit();
            } else if (state.selectedBabel) {
                deselectBabel();
            }
            break;
        case 'delete':
        case 'backspace':
            if (state.selectedBabel && !comparisonOverlay.classList.contains('active') && !editOverlay.classList.contains('active')) {
                handleDelete();
            }
            break;
        case 'e':
            if (state.selectedBabel && !comparisonOverlay.classList.contains('active') && !editOverlay.classList.contains('active')) {
                openEdit(state.selectedBabel);
            }
            break;
        case ' ':
            e.preventDefault();
            togglePhysics();
            break;
    }
}

function handleKeyUp(e) {
    if (e.key.toLowerCase() === 'r' && state.isCreating && !creationForm.classList.contains('active')) {
        cancelCreation();
    }
}

// ============================================
// CREATION ANIMATION
// ============================================

function startCreation() {
    state.isCreating = true;
    state.creationStartTime = Date.now();
    state.currentColorIndex = 0;

    creationOverlay.classList.add('active');
    animateCreation();
}

function animateCreation() {
    if (!state.isCreating) return;

    const elapsed = Date.now() - state.creationStartTime;
    const progress = Math.min(elapsed / 2000, 1);

    // Circle size grows
    const maxSize = Math.max(window.innerWidth, window.innerHeight) * 2;
    const size = progress * maxSize * 0.1;
    creationCircle.style.width = size + 'px';
    creationCircle.style.height = size + 'px';

    // Color cycles through dark presets
    const colorProgress = (elapsed / 300) % COLOR_PRESETS.dark.length;
    const colorIndex = Math.floor(colorProgress);
    creationCircle.style.backgroundColor = COLOR_PRESETS.dark[colorIndex];

    if (progress >= 1) {
        // Animation complete - show form
        showCreationForm();
    } else {
        state.creationAnimationFrame = requestAnimationFrame(animateCreation);
    }
}

function showCreationForm() {
    creationOverlay.classList.remove('active');
    creationCircle.style.width = '0';
    creationCircle.style.height = '0';

    // Populate similar babels
    updateSimilarBabelsSelector();

    // Reset form
    document.getElementById('babel-title').value = '';
    document.getElementById('babel-description').value = '';
    state.selectedColor = COLOR_PRESETS.bright[0];
    state.selectedSimilarBabels = [];
    setupColorPalette();

    creationForm.classList.add('active');
    //document.getElementById('babel-title').focus();
}

function updateSimilarBabelsSelector() {
    similarBabels.innerHTML = '';

    if (state.babels.length === 0) {
        similarBabels.innerHTML = '<span class="babel-chips-empty">No other babels yet</span>';
        return;
    }

    state.babels.forEach(babel => {
        const chip = document.createElement('div');
        chip.className = 'babel-chip';
        chip.textContent = babel.title || 'Untitled';
        chip.style.borderColor = babel.color;
        chip.addEventListener('click', () => {
            chip.classList.toggle('selected');
            if (chip.classList.contains('selected')) {
                state.selectedSimilarBabels.push(babel.id);
            } else {
                state.selectedSimilarBabels = state.selectedSimilarBabels.filter(id => id !== babel.id);
            }
        });
        similarBabels.appendChild(chip);
    });
}

function cancelCreation() {
    state.isCreating = false;
    if (state.creationAnimationFrame) {
        cancelAnimationFrame(state.creationAnimationFrame);
    }

    // Reverse animation
    const currentSize = parseFloat(creationCircle.style.width) || 0;
    animateReverse(currentSize);
}

function animateReverse(size) {
    if (size <= 0) {
        creationOverlay.classList.remove('active');
        creationForm.classList.remove('active');
        return;
    }

    const newSize = size - (size * 0.1 + 10);
    creationCircle.style.width = Math.max(0, newSize) + 'px';
    creationCircle.style.height = Math.max(0, newSize) + 'px';

    requestAnimationFrame(() => animateReverse(newSize));
}

function createBabel() {
    const title = document.getElementById('babel-title').value.trim();
    const description = document.getElementById('babel-description').value.trim();

    const newBabel = {
        id: Date.now().toString(),
        title: title || 'Untitled',
        description: description,
        color: state.selectedColor
    };

    state.babels.push(newBabel);

    // Create edges to selected similar babels
    // New babel has no incoming edges, so no cycle risk
    state.selectedSimilarBabels.forEach(targetId => {
        state.edges.push({
            source: newBabel.id,
            target: targetId,
            id: `${newBabel.id}-${targetId}`
        });
    });

    // Prune any transitive edges created by the new connections
    if (state.selectedSimilarBabels.length > 0) {
        pruneTransitiveEdges();
    }

    updateGraph();
    updateHintText();
    creationForm.classList.remove('active');
    state.isCreating = false;
}

// ============================================
// GRAPH UPDATES
// ============================================

function updateGraph() {
    // Get valid node IDs
    const validNodeIds = new Set(state.babels.map(b => b.id));

    // Filter edges to only those with valid nodes
    const validEdges = state.edges.filter(e =>
        validNodeIds.has(e.source) && validNodeIds.has(e.target)
    );

    // Identify mutual edges (associations) - these create cycles in DAG
    const edgePairs = new Map();
    validEdges.forEach(edge => {
        const forwardKey = `${edge.source}-${edge.target}`;
        const reverseKey = `${edge.target}-${edge.source}`;

        if (edgePairs.has(reverseKey)) {
            // This is the second edge of a pair - both are mutual
            edge.isMutual = true;
            const reverseEdge = validEdges.find(e =>
                e.source === edge.target && e.target === edge.source
            );
            if (reverseEdge) reverseEdge.isMutual = true;
        } else {
            edgePairs.set(forwardKey, edge);
            edge.isMutual = false;
        }
    });

    // Re-check all edges for mutual status
    validEdges.forEach(edge => {
        const reverseExists = validEdges.some(e =>
            e.source === edge.target && e.target === edge.source
        );
        edge.isMutual = reverseExists;
    });

    // For DAG: only use non-mutual (single direction) edges
    // Associations would create cycles and break the layout
    const dagEdges = validEdges.filter(e => !e.isMutual);

    const graphData = {
        nodes: state.babels.map(b => ({
            id: b.id,
            title: b.title,
            description: b.description,
            color: b.color
        })),
        // Only pass DAG-compatible edges (no associations/cycles)
        links: dagEdges.map(e => ({
            source: e.source,
            target: e.target,
            isMutual: false
        }))
    };

    console.log('Updating graph - nodes:', graphData.nodes.length, 'links:', graphData.links.length);
    Graph.graphData(graphData);
}

function updateHintText() {
    if (state.babels.length > 0) {
        hintText.classList.add('hidden');
    } else {
        hintText.classList.remove('hidden');
    }
}

// ============================================
// NODE INTERACTIONS
// ============================================

function handleNodeClick(node, event) {
    // Double-click detection
    const now = Date.now();
    if (node.__lastClick && (now - node.__lastClick) < 300) {
        // Double-click - focus camera
        Graph.cameraPosition(
            { x: node.x, y: node.y, z: node.z + 100 },
            { x: node.x, y: node.y, z: node.z },
            1000
        );
        return;
    }
    node.__lastClick = now;

    // Single click - select
    selectBabel(node);
}

function selectBabel(node) {
    const babel = state.babels.find(b => b.id === node.id);
    state.selectedBabel = babel;
    Graph.nodeThreeObject(Graph.nodeThreeObject()); // Force re-render
}

function deselectBabel() {
    state.selectedBabel = null;
    state.deleteWarningBabel = null;
    if (state.deleteWarningTimeout) {
        clearTimeout(state.deleteWarningTimeout);
    }
    Graph.nodeThreeObject(Graph.nodeThreeObject());
}

function handleNodeRightClick(node, event) {
    event.preventDefault();
    event.stopPropagation();

    const babel = state.babels.find(b => b.id === node.id);
    console.log('Right-clicked babel:', babel.title, 'Current comparison babels:', state.comparisonBabels.length);

    if (state.comparisonBabels.length === 0) {
        state.comparisonBabels.push(babel);
        // Visual feedback - select the first babel
        state.selectedBabel = babel;
        Graph.nodeThreeObject(Graph.nodeThreeObject()); // Force re-render
        console.log('First babel selected for comparison:', babel.title);
    } else if (state.comparisonBabels.length === 1 && state.comparisonBabels[0].id !== babel.id) {
        state.comparisonBabels.push(babel);
        console.log('Opening comparison between:', state.comparisonBabels[0].title, 'and', babel.title);
        openComparison();
    }
}

function handleBackgroundClick(event) {
    console.log('Background clicked');
    deselectBabel();
    state.comparisonBabels = [];
}

// ============================================
// COMPARISON MODE
// ============================================

function openComparison() {
    const [left, right] = state.comparisonBabels;

    // Set up left panel
    const leftPanel = document.getElementById('babel-panel-left');
    leftPanel.querySelector('.babel-sphere').style.backgroundColor = left.color;
    leftPanel.querySelector('.babel-edit-title').value = left.title;
    leftPanel.querySelector('.babel-edit-description').value = left.description;
    leftPanel.dataset.babelId = left.id;

    // Set up right panel
    const rightPanel = document.getElementById('babel-panel-right');
    rightPanel.querySelector('.babel-sphere').style.backgroundColor = right.color;
    rightPanel.querySelector('.babel-edit-title').value = right.title;
    rightPanel.querySelector('.babel-edit-description').value = right.description;
    rightPanel.dataset.babelId = right.id;

    // Check existing edges
    updateEdgeIndicators();

    comparisonOverlay.classList.add('active');

    // Animate panels opening
    setTimeout(() => {
        leftPanel.classList.add('open');
        rightPanel.classList.add('open');
    }, 100);
}

function updateEdgeIndicators() {
    const [left, right] = state.comparisonBabels;

    const leftToRight = state.edges.find(e => e.source === left.id && e.target === right.id);
    const rightToLeft = state.edges.find(e => e.source === right.id && e.target === left.id);

    document.querySelector('.edge-arrow.left-to-right').classList.toggle('active', !!leftToRight);
    document.querySelector('.edge-arrow.right-to-left').classList.toggle('active', !!rightToLeft);
}

function toggleEdge(direction) {
    const [left, right] = state.comparisonBabels;
    const sourceId = direction === 'left-to-right' ? left.id : right.id;
    const targetId = direction === 'left-to-right' ? right.id : left.id;

    const existingEdgeIndex = state.edges.findIndex(e => e.source === sourceId && e.target === targetId);

    if (existingEdgeIndex >= 0) {
        // Removing an edge - always allowed
        state.edges.splice(existingEdgeIndex, 1);
    } else {
        // Adding an edge - check for cycle first
        if (wouldCreateCycle(sourceId, targetId)) {
            console.log(`Cannot create edge ${sourceId} -> ${targetId}: would create cycle`);
            // Could show a visual warning here
            return;
        }

        // Add the edge
        state.edges.push({
            source: sourceId,
            target: targetId,
            id: `${sourceId}-${targetId}`
        });

        // Prune any transitive edges that this new edge makes redundant
        pruneTransitiveEdges();
    }

    updateEdgeIndicators();
}

function closeComparison() {
    // Save edits
    const leftPanel = document.getElementById('babel-panel-left');
    const rightPanel = document.getElementById('babel-panel-right');

    const leftBabel = state.babels.find(b => b.id === leftPanel.dataset.babelId);
    const rightBabel = state.babels.find(b => b.id === rightPanel.dataset.babelId);

    if (leftBabel) {
        leftBabel.title = leftPanel.querySelector('.babel-edit-title').value.trim() || 'Untitled';
        leftBabel.description = leftPanel.querySelector('.babel-edit-description').value.trim();
    }

    if (rightBabel) {
        rightBabel.title = rightPanel.querySelector('.babel-edit-title').value.trim() || 'Untitled';
        rightBabel.description = rightPanel.querySelector('.babel-edit-description').value.trim();
    }

    // Animate slide out
    leftPanel.classList.remove('open');
    rightPanel.classList.remove('open');

    setTimeout(() => {
        comparisonOverlay.classList.remove('active');
        state.comparisonBabels = [];
        updateGraph();
    }, 300);
}

// ============================================
// EDIT MODE
// ============================================

function openEdit(babel) {
    state.editingBabel = babel;

    const panel = document.getElementById('edit-babel-panel');
    panel.querySelector('.babel-sphere').style.backgroundColor = babel.color;
    document.getElementById('edit-title').value = babel.title;
    document.getElementById('edit-description').value = babel.description;

    // Set selected color in palette
    const editPalette = document.getElementById('edit-color-palette');
    const colorIndex = COLOR_PRESETS.bright.indexOf(babel.color);
    editPalette.querySelectorAll('.color-swatch').forEach((s, i) => {
        s.classList.toggle('selected', i === colorIndex);
    });
    state.selectedColor = babel.color;

    editOverlay.classList.add('active');

    setTimeout(() => {
        panel.classList.add('open');
    }, 100);
}

function closeEdit() {
    const babel = state.editingBabel;
    if (babel) {
        babel.title = document.getElementById('edit-title').value.trim() || 'Untitled';
        babel.description = document.getElementById('edit-description').value.trim();
        babel.color = state.selectedColor;
    }

    const panel = document.getElementById('edit-babel-panel');
    panel.classList.remove('open');

    setTimeout(() => {
        editOverlay.classList.remove('active');
        state.editingBabel = null;
        updateGraph();
    }, 300);
}

// ============================================
// DELETE
// ============================================

function handleDelete() {
    if (state.deleteWarningBabel && state.deleteWarningBabel.id === state.selectedBabel.id) {
        // Second delete - confirm
        deleteBabel(state.selectedBabel);
    } else {
        // First delete - show warning
        state.deleteWarningBabel = state.selectedBabel;
        Graph.nodeThreeObject(Graph.nodeThreeObject());

        state.deleteWarningTimeout = setTimeout(() => {
            state.deleteWarningBabel = null;
            Graph.nodeThreeObject(Graph.nodeThreeObject());
        }, 2000);
    }
}

function deleteBabel(babel) {
    // Remove babel
    state.babels = state.babels.filter(b => b.id !== babel.id);

    // Remove all edges involving this babel
    state.edges = state.edges.filter(e => e.source !== babel.id && e.target !== babel.id);

    state.selectedBabel = null;
    state.deleteWarningBabel = null;
    if (state.deleteWarningTimeout) {
        clearTimeout(state.deleteWarningTimeout);
    }

    updateGraph();
    updateHintText();
}

// ============================================
// PHYSICS
// ============================================

function togglePhysics() {
    state.isPhysicsPaused = !state.isPhysicsPaused;

    if (state.isPhysicsPaused) {
        Graph.pauseAnimation();
    } else {
        Graph.resumeAnimation();
    }
}

// ============================================
// PERSISTENCE
// ============================================

function saveToLocalStorage() {
    const data = {
        babels: state.babels,
        edges: state.edges
    };

    localStorage.setItem('babel-graph', JSON.stringify(data));

    // Show toast
    saveToast.classList.add('visible');
    setTimeout(() => {
        saveToast.classList.remove('visible');
    }, 2000);
}

function loadFromLocalStorage() {
    const saved = localStorage.getItem('babel-graph');

    if (saved) {
        try {
            const data = JSON.parse(saved);
            state.babels = data.babels || [];
            state.edges = data.edges || [];
        } catch (e) {
            console.error('Failed to load saved data:', e);
        }
    }
}

// ============================================
// INIT
// ============================================

init();
