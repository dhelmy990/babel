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
        .nodeThreeObject(node => Rendering.createNodeObject(node, {
            isSelected: State.selectedBabel?.id === node.id,
            isDeleteWarning: State.deleteWarningBabel?.id === node.id
        }))
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
            LevelCircles.updateLevelCircles(Graph);
        });

    return Graph;
}

// ============================================
// NODE INTERACTION HANDLERS
// ============================================

function handleNodeClick(node, event) {
    const now = Date.now();
    if (node.__lastClick && (now - node.__lastClick) < Config.ui.doubleClickThreshold) {
        Graph.cameraPosition(
            { x: node.x, y: node.y, z: node.z + 100 },
            { x: node.x, y: node.y, z: node.z },
            1000
        );
        return;
    }
    node.__lastClick = now;

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
    if (!LevelCircles.dragState.isDragging || LevelCircles.dragState.node?.id !== node.id) {
        node.__originalY = node.y;
        node.__originalX = node.x;
        node.__originalZ = node.z;
        LevelCircles.startDrag(node, Graph);
    }

    node.fx = node.x;
    node.fy = node.y;
    node.fz = node.z;

    LevelCircles.updateDraggedLevelCircle(Graph, node.x, node.z);
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

    LevelCircles.endDrag(Graph);

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
    if (hoveredSphere) {
        Rendering.setHoverState(hoveredSphere.material, false);
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

    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    const camera = Graph.camera();
    raycaster.setFromCamera(mouse, camera);

    const scene = Graph.scene();
    const intersects = raycaster.intersectObjects(scene.children, true);

    for (const intersect of intersects) {
        const obj = intersect.object;

        if (obj.userData && obj.userData.isMainSphere) {
            if (hoveredSphere !== obj) {
                if (hoveredSphere) {
                    Rendering.setHoverState(hoveredSphere.material, false);
                }
                hoveredSphere = obj;
                Rendering.setHoverState(obj.material, true);
            }

            const localPoint = obj.worldToLocal(intersect.point.clone());
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
        GraphUtils.pruneTransitiveEdges(State.edges)
            .forEach(e => State.removeEdge(e.source, e.target));
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
        if (GraphUtils.wouldCreateCycle(sourceId, targetId, State.edges)) {
            console.log('Cannot create edge: would create cycle');
            return;
        }
        State.addEdge(sourceId, targetId);
        GraphUtils.pruneTransitiveEdges(State.edges)
            .forEach(e => State.removeEdge(e.source, e.target));
    }

    UI.updateEdgeIndicators();
}

function resetCamera() {
    const pos = Graph.cameraPosition();
    const distance = Math.sqrt(pos.x * pos.x + pos.y * pos.y + pos.z * pos.z);
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
    const validEdges = State.getValidEdges();
    const dagEdges = GraphUtils.getDagEdges(validEdges);

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

    setTimeout(() => LevelCircles.updateLevelCircles(Graph), 500);
    setTimeout(() => LevelCircles.updateLevelCircles(Graph), 1500);
}

// ============================================
// INITIALIZATION
// ============================================

async function init() {
    UI.init();
    initGraph();
    Rendering.setupLighting(Graph.scene());
    await Persistence.load();
    GraphUtils.cleanGraphOnLoad();
    UI.setupColorPalette();

    UI.setupEventListeners();

    document.addEventListener('babel:create', createBabel);
    document.addEventListener('babel:toggle-edge', e => toggleEdge(e.detail.direction));
    document.addEventListener('babel:deselect', deselectBabel);
    document.addEventListener('babel:delete', handleDelete);
    document.addEventListener('babel:reset-camera', resetCamera);
    document.addEventListener('babel:save', () => Persistence.save());

    document.addEventListener('keydown', async (e) => {
        if (e.ctrlKey && e.shiftKey && e.key === 'I') {
            e.preventDefault();
            const ok = await Persistence.importJSON();
            if (ok) { updateGraph(); UI.updateHintText(); }
        }
    });
    document.addEventListener('babel:comparison-closed', updateGraph);
    document.addEventListener('babel:edit-closed', updateGraph);

    updateGraph();
    UI.updateHintText();
    Animation.startLoop(Graph);

    UI.elements.graphContainer.addEventListener('mousemove', handleMouseMove);

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
