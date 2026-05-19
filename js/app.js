// ============================================
// BABEL - Knowledge Graph System
// Main Application Entry Point
// ============================================

let Graph = null;

// Raycasting state for hover hole effect
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let hoveredNode = null;
let hoveredSphere = null;

/**
 * Initialize ForceGraph3D with all configurations
 */
function initGraph() {
    Graph = ForceGraph3D()(UI.elements.graphContainer)
        .backgroundColor(Config.graph.backgroundColor)
        .nodeThreeObject(node => Rendering.createNodeObject(node))
        .nodeLabel(node => node.title || 'Untitled')
        .linkWidth(0)
        .linkColor(() => 'rgba(0,0,0,0)')
        .linkThreeObjectExtend(true)
        .linkThreeObject(link => {
            const group = new THREE.Group();
            group.userData.link = link;
            return group;
        })
        .linkPositionUpdate((obj, { start, end }, link) => {
            Animation.updateEdge(obj, start, end, link);
        })
        .onNodeClick(handleNodeClick)
        .onNodeRightClick(handleNodeRightClick)
        .onNodeHover(handleNodeHover)
        .onBackgroundClick(handleBackgroundClick)
        .onBackgroundRightClick(() => {
            console.log('Background right-clicked - preserving comparison state');
        })
        .enableNodeDrag(true)
        .onNodeDrag(handleNodeDrag)
        .onNodeDragEnd(handleNodeDragEnd)
        .dagMode(Config.graph.dagMode)
        .dagLevelDistance(Config.graph.dagLevelDistance)
        .d3AlphaDecay(Config.physics.alphaDecay)
        .d3VelocityDecay(Config.physics.velocityDecay)
        .d3Force('charge', d3.forceManyBody()
            .strength(Config.physics.chargeStrength)
            .distanceMax(Config.physics.chargeDistanceMax))
        .d3Force('link', d3.forceLink().strength(Config.physics.linkStrength))
        .onDagError(() => {
            console.warn('DAG layout error - graph may have cycles');
        })
        .onEngineStop(() => {
            Animation.updateLevelCircles(Graph);
        });

    return Graph;
}

// ============================================
// NODE INTERACTION HANDLERS
// ============================================

function handleNodeClick(node, event) {
    const now = Date.now();
    if (node.__lastClick && (now - node.__lastClick) < Config.ui.doubleClickThreshold) {
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
    State.selectedBabel = State.getBabel(node.id);
    Graph.nodeThreeObject(Graph.nodeThreeObject());
}

function handleNodeRightClick(node, event) {
    event.preventDefault();
    event.stopPropagation();

    const babel = State.getBabel(node.id);
    console.log('Right-clicked:', babel.title);

    if (State.comparisonBabels.length === 0) {
        State.comparisonBabels.push(babel);
        State.selectedBabel = babel;
        Graph.nodeThreeObject(Graph.nodeThreeObject());
    } else if (State.comparisonBabels.length === 1 && State.comparisonBabels[0].id !== babel.id) {
        State.comparisonBabels.push(babel);
        UI.openComparison();
    }
}

function handleBackgroundClick() {
    deselectBabel();
    State.comparisonBabels = [];
}

function handleNodeDrag(node) {
    // Detect drag start (first call for this drag)
    if (!Animation.dragState.isDragging || Animation.dragState.node?.id !== node.id) {
        // Store original position for return animation
        node.__originalY = node.y;
        node.__originalX = node.x;
        node.__originalZ = node.z;
        // Start tracking for circle animation
        Animation.startDrag(node, Graph);
    }

    node.fx = node.x;
    node.fy = node.y;
    node.fz = node.z;

    // Update level circle with current drag position
    Animation.updateDraggedLevelCircle(Graph, node.x, node.z);
}

function handleNodeDragEnd(node) {
    const originalY = node.__originalY;
    const originalX = node.__originalX;
    const originalZ = node.__originalZ;

    node.__originalY = undefined;
    node.__originalX = undefined;
    node.__originalZ = undefined;

    node.fx = undefined;
    node.fz = undefined;
    node.fy = node.y;

    // Start circle return animation
    Animation.endDrag(Graph);

    if (originalY !== undefined) {
        const startY = node.y;
        const startTime = Date.now();
        const duration = Config.animation.dragReturnDuration;

        function animateReturn() {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);

            node.fy = startY + (originalY - startY) * eased;

            if (progress < 1) {
                requestAnimationFrame(animateReturn);
            }
        }
        requestAnimationFrame(animateReturn);
    }
}

function handleNodeHover(node) {
    // Clear previous hover state
    if (hoveredSphere) {
        const prevMaterial = hoveredSphere.material;
        Rendering.setHoverState(prevMaterial, false);
        hoveredSphere = null;
    }
    hoveredNode = null;

    if (node) {
        hoveredNode = node;
    }
}

/**
 * Handle mouse move for raycasting hole position
 */
function handleMouseMove(event) {
    if (!hoveredNode || !Graph) return;

    const container = UI.elements.graphContainer;
    const rect = container.getBoundingClientRect();

    // Convert mouse to normalized device coordinates (-1 to +1)
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    // Get camera and update raycaster
    const camera = Graph.camera();
    raycaster.setFromCamera(mouse, camera);

    // Find all node objects and raycast against them
    const scene = Graph.scene();
    const intersects = raycaster.intersectObjects(scene.children, true);

    for (const intersect of intersects) {
        const obj = intersect.object;

        // Check if this is a main sphere (tagged in createNodeObject)
        if (obj.userData && obj.userData.isMainSphere) {
            // Store reference for hover state management
            if (hoveredSphere !== obj) {
                // Clear old sphere's hover state
                if (hoveredSphere) {
                    Rendering.setHoverState(hoveredSphere.material, false);
                }
                // Set new sphere's hover state
                hoveredSphere = obj;
                Rendering.setHoverState(obj.material, true);
            }

            // Convert world intersection point to local coordinates
            const localPoint = obj.worldToLocal(intersect.point.clone());

            // Convert to UV and update shader
            const uv = Rendering.pointToUV(localPoint, Config.node.radius);
            Rendering.updateHolePosition(uv);
            break;
        }
    }
}

// ============================================
// BABEL OPERATIONS
// ============================================

function deselectBabel() {
    State.selectedBabel = null;
    State.deleteWarningBabel = null;
    if (State.deleteWarningTimeout) {
        clearTimeout(State.deleteWarningTimeout);
    }
    Graph.nodeThreeObject(Graph.nodeThreeObject());
}

function createBabel() {
    const title = document.getElementById('babel-title').value.trim();
    const description = document.getElementById('babel-description').value.trim();

    const newBabel = {
        id: Date.now().toString(),
        title: title || 'Untitled',
        description,
        color: State.selectedColor
    };

    State.addBabel(newBabel);

    State.selectedSimilarBabels.forEach(targetId => {
        State.addEdge(newBabel.id, targetId);
    });

    if (State.selectedSimilarBabels.length > 0) {
        GraphUtils.pruneTransitiveEdges();
    }

    updateGraph();
    UI.updateHintText();
    UI.elements.creationForm.classList.remove('active');
    State.isCreating = false;
}

function handleDelete() {
    if (State.deleteWarningBabel?.id === State.selectedBabel.id) {
        State.removeBabel(State.selectedBabel.id);
        State.selectedBabel = null;
        State.deleteWarningBabel = null;
        clearTimeout(State.deleteWarningTimeout);
        updateGraph();
        UI.updateHintText();
    } else {
        State.deleteWarningBabel = State.selectedBabel;
        Graph.nodeThreeObject(Graph.nodeThreeObject());

        State.deleteWarningTimeout = setTimeout(() => {
            State.deleteWarningBabel = null;
            Graph.nodeThreeObject(Graph.nodeThreeObject());
        }, Config.ui.deleteWarningDuration);
    }
}

function toggleEdge(direction) {
    const [left, right] = State.comparisonBabels;
    const sourceId = direction === 'left-to-right' ? left.id : right.id;
    const targetId = direction === 'left-to-right' ? right.id : left.id;

    if (State.hasEdge(sourceId, targetId)) {
        State.removeEdge(sourceId, targetId);
    } else {
        if (GraphUtils.wouldCreateCycle(sourceId, targetId)) {
            console.log('Cannot create edge: would create cycle');
            return;
        }
        State.addEdge(sourceId, targetId);
        GraphUtils.pruneTransitiveEdges();
    }

    UI.updateEdgeIndicators();
}

function resetCamera() {
    // Get current camera position
    const pos = Graph.cameraPosition();

    // Calculate distance from origin (preserve zoom level)
    const distance = Math.sqrt(pos.x * pos.x + pos.y * pos.y + pos.z * pos.z);

    // Move camera to (0, 0, distance) looking at origin — this is the default "upright" view
    Graph.cameraPosition(
        { x: 0, y: 0, z: distance },
        { x: 0, y: 0, z: 0 },
        1000
    );
}

// ============================================
// GRAPH UPDATE
// ============================================

function updateGraph() {
    const dagEdges = GraphUtils.getDagEdges();

    const graphData = {
        nodes: State.babels.map(b => ({
            id: b.id,
            title: b.title,
            description: b.description,
            color: b.color
        })),
        links: dagEdges.map(e => ({
            source: e.source,
            target: e.target,
            isMutual: false
        }))
    };

    console.log('Updating graph:', graphData.nodes.length, 'nodes,', graphData.links.length, 'links');
    Graph.graphData(graphData);

    setTimeout(() => Animation.updateLevelCircles(Graph), 500);
    setTimeout(() => Animation.updateLevelCircles(Graph), 1500);
}

// ============================================
// INITIALIZATION
// ============================================

function init() {
    UI.init();
    initGraph();
    Rendering.setupLighting(Graph.scene());
    Persistence.load();
    GraphUtils.cleanGraphOnLoad();
    UI.setupColorPalette();

    UI.setupEventListeners({
        createBabel,
        cancelCreation: () => UI.cancelCreation(),
        startCreation: () => UI.startCreation(),
        closeComparison: () => UI.closeComparison(updateGraph),
        closeEdit: () => UI.closeEdit(updateGraph),
        openEdit: (babel) => UI.openEdit(babel),
        toggleEdge,
        deselectBabel,
        handleDelete,
        resetCamera,
        save: () => Persistence.save()
    });

    updateGraph();
    UI.updateHintText();
    Animation.startLoop(Graph);

    // Add mousemove listener for hole effect raycasting
    UI.elements.graphContainer.addEventListener('mousemove', handleMouseMove);

    // Clear hover state when mouse leaves the container
    UI.elements.graphContainer.addEventListener('mouseleave', () => {
        if (hoveredSphere) {
            Rendering.setHoverState(hoveredSphere.material, false);
            hoveredSphere = null;
        }
        hoveredNode = null;
    });
}

// Start the application
init();
