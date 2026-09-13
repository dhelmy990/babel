/* The API owns every edge. ForceGraph receives disposable clones only. */
(() => {
    'use strict';
    const container = document.getElementById('website-graph');
    const status = document.getElementById('graph-status');
    const retry = document.getElementById('graph-retry');
    const reset = document.getElementById('graph-reset');
    const source = document.getElementById('edge-source');
    const target = document.getElementById('edge-target');
    let canonical = null, graph = null, stopAnimation = null;
    let hoveredNode = null, hoveredSphere = null, draggedUntil = 0;
    let unavailable = false, busy = false, holdTimer = null;
    const timers = new Set(), frames = new Set();
    let raycaster, mouse;

    function clearHover() {
        if (hoveredSphere) Rendering.setHoverState(hoveredSphere.material, false);
        hoveredSphere = null;
        hoveredNode = null;
    }
    function after(callback, delay) {
        const id = setTimeout(() => { timers.delete(id); if (graph) callback(); }, delay);
        timers.add(id);
    }
    function frame(callback) {
        const id = requestAnimationFrame(() => { frames.delete(id); if (graph) callback(); });
        frames.add(id);
    }
    function disposeGraph() {
        clearHover();
        if (stopAnimation) stopAnimation();
        stopAnimation = null;
        timers.forEach(clearTimeout); timers.clear();
        frames.forEach(cancelAnimationFrame); frames.clear();
        if (typeof LevelCircles !== 'undefined') LevelCircles.dispose(graph);
        if (graph) {
            graph.pauseAnimation();
            graph._destructor();
            graph = null;
        }
        container.replaceChildren();
        reset.hidden = true;
    }
    function fallback() {
        unavailable = true;
        disposeGraph();
        container.hidden = true;
        status.textContent = 'The galaxy view is unavailable. Explore the article list below.';
    }
    function preview(node) {
        const box = document.createElement('div');
        box.className = 'graph-preview';
        const title = document.createElement('strong');
        const excerpt = document.createElement('p');
        title.textContent = node.title;
        excerpt.textContent = node.excerpt;
        box.append(title, excerpt);
        return box.outerHTML;
    }
    function updateList(data) {
        const list = document.getElementById('graph-articles');
        list.replaceChildren();
        data.nodes.forEach(node => {
            const item = document.createElement('li');
            const link = document.createElement('a');
            link.href = '/' + node.slug;
            link.textContent = node.title;
            const excerpt = document.createElement('p');
            excerpt.textContent = node.excerpt;
            item.append(link, excerpt); list.append(item);
        });
        if (!data.nodes.length) list.textContent = 'No articles yet.';
        for (const select of [source, target]) {
            if (!select) continue;
            const selected = select.value;
            select.replaceChildren(new Option(select === source ? 'Choose source' : 'Choose target', ''));
            data.nodes.forEach(node => select.add(new Option(node.title, node.id)));
            select.value = data.nodes.some(node => node.id === selected) ? selected : '';
        }
    }
    function selectionChanged() {
        clearHover();
        if (graph) graph.nodeThreeObject(graph.nodeThreeObject());
    }
    function rightClick(node, event) {
        if (!source) return;
        event.preventDefault();
        if (!source.value || target.value) { source.value = node.id; target.value = ''; }
        else if (source.value !== node.id) target.value = node.id;
        selectionChanged();
    }
    function drag(node) {
        draggedUntil = Date.now() + 500;
        clearHover();
        if (!LevelCircles.dragState.isDragging || LevelCircles.dragState.node?.id !== node.id) {
            node.__originalY = node.y;
            LevelCircles.startDrag(node, graph);
        }
        node.fx = node.x; node.fy = node.y; node.fz = node.z;
        LevelCircles.updateDraggedLevelCircle(graph, node.x, node.z);
    }
    function endDrag(node) {
        draggedUntil = Date.now() + 500;
        const originalY = node.__originalY, startY = node.y, start = Date.now();
        delete node.__originalY;
        node.fx = undefined; node.fz = undefined; node.fy = node.y;
        LevelCircles.endDrag(graph);
        if (originalY === undefined) return;
        const restore = () => {
            const progress = Math.min((Date.now() - start) / Config.animation.dragReturnDuration, 1);
            node.fy = startY + (originalY - startY) * (1 - Math.pow(1 - progress, 3));
            if (progress < 1) frame(restore);
        };
        frame(restore);
    }
    function buildGraph() {
        if (typeof THREE === 'undefined' || typeof d3 === 'undefined' || typeof ForceGraph3D === 'undefined') throw new Error('Missing renderer');
        raycaster = new THREE.Raycaster(); mouse = new THREE.Vector2();
        graph = ForceGraph3D()(container)
            .width(container.clientWidth).height(580)
            .backgroundColor(Config.graph.backgroundColor)
            .nodeThreeObject(node => Rendering.createNodeObject(node, {isSelected: !!source && [source.value, target.value].includes(node.id)}))
            .nodeLabel(preview)
            .linkWidth(0).linkColor(() => 'rgba(0,0,0,0)')
            .linkThreeObjectExtend(true)
            .linkThreeObject(link => { const group = new THREE.Group(); group.userData.link = link; return group; })
            // Animation.startLoop updates each edge once per frame.
            .linkPositionUpdate(() => true)
            .onNodeClick(node => { if (Date.now() > draggedUntil) location.assign('/' + node.slug); })
            .onNodeRightClick(rightClick)
            .onNodeHover(node => { clearHover(); hoveredNode = node; })
            .onBackgroundClick(() => { if (source) { source.value = ''; target.value = ''; selectionChanged(); } })
            .enableNodeDrag(true).onNodeDrag(drag).onNodeDragEnd(endDrag)
            .dagMode(Config.graph.dagMode).dagLevelDistance(Config.graph.dagLevelDistance)
            .d3AlphaDecay(Config.physics.alphaDecay).d3VelocityDecay(Config.physics.velocityDecay)
            .d3Force('charge', d3.forceManyBody().strength(Config.physics.chargeStrength).distanceMax(Config.physics.chargeDistanceMax))
            .d3Force('link', d3.forceLink().strength(Config.physics.linkStrength))
            .onDagError(() => { status.textContent = 'The galaxy could not arrange these links.'; })
            .onEngineStop(() => { if (graph) LevelCircles.updateLevelCircles(graph); });
        Rendering.setupLighting(graph.scene());
        graph.renderer().domElement.addEventListener('webglcontextlost', event => { event.preventDefault(); fallback(); });
        stopAnimation = Animation.startLoop(graph);
        reset.hidden = false;
    }
    function display(data) {
        clearHover();
        canonical = data;
        State.nodes = new Map(data.nodes.map(node => [node.id, node]));
        updateList(data);
        if (unavailable) { fallback(); return; }
        try {
            if (!graph) buildGraph();
            graph.graphData({ nodes: data.nodes.map(node => ({...node})), links: data.edges.map(edge => ({...edge})) });
            after(() => LevelCircles.updateLevelCircles(graph), 500);
            after(() => LevelCircles.updateLevelCircles(graph), 1500);
            status.textContent = `${data.nodes.length} articles · ${data.edges.length} read-before links`;
        } catch (_) { fallback(); }
    }
    async function load() {
        retry.hidden = true;
        try {
            const response = await fetch('/api/graph', {headers: {Accept: 'application/json'}});
            if (!response.ok) throw new Error();
            const data = await response.json();
            if (!Array.isArray(data.nodes) || !Array.isArray(data.edges)) throw new Error();
            display(data);
        } catch (_) {
            status.textContent = 'Could not load the galaxy. You can still use the article links below.';
            retry.hidden = false;
        }
    }
    async function mutate(remove) {
        if (busy) return;
        if (!source.value || !target.value) { status.textContent = 'Choose the article to read first and the article to read next.'; return; }
        busy = true;
        const buttons = document.querySelectorAll('#edge-controls button');
        buttons.forEach(button => button.disabled = true);
        try {
            const csrf = document.cookie.split('; ').find(cookie => cookie.startsWith('csrftoken='))?.slice(10) || '';
            const response = await fetch(remove ? `/api/edges/${source.value}/${target.value}` : '/api/edges', {
                method: remove ? 'DELETE' : 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': decodeURIComponent(csrf)},
                ...(remove ? {} : {body: JSON.stringify({source: source.value, target: target.value})})
            });
            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.error?.message || 'The link could not be saved.');
            }
            await load();
        } catch (error) { status.textContent = error.message || 'The link could not be saved.'; }
        finally { busy = false; buttons.forEach(button => button.disabled = false); }
    }
    container.addEventListener('mousemove', event => {
        if (!hoveredNode || !graph) return;
        const rect = container.getBoundingClientRect();
        mouse.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
        raycaster.setFromCamera(mouse, graph.camera());
        const hit = raycaster.intersectObjects(graph.scene().children, true).find(item => item.object.userData.isMainSphere && item.object.userData.nodeId === hoveredNode.id);
        if (hit) {
            if (hoveredSphere !== hit.object) {
                if (hoveredSphere) Rendering.setHoverState(hoveredSphere.material, false);
                hoveredSphere = hit.object;
                Rendering.setHoverState(hoveredSphere.material, true);
            }
            Rendering.updateHolePosition(hit.uv);
        }
    });
    container.addEventListener('mouseleave', clearHover);
    window.addEventListener('resize', () => { if (graph) graph.width(container.clientWidth); });
    reset.addEventListener('click', () => {
        if (!graph) return;
        const position = graph.cameraPosition();
        graph.cameraPosition({x: 0, y: 0, z: Math.hypot(position.x, position.y, position.z)}, {x: 0, y: 0, z: 0}, 1000);
    });
    const cancelHold = () => { clearTimeout(holdTimer); holdTimer = null; };
    if (source) {
        source.addEventListener('change', selectionChanged);
        target.addEventListener('change', selectionChanged);
        document.getElementById('edge-add').addEventListener('click', () => mutate(false));
        document.getElementById('edge-remove').addEventListener('click', () => mutate(true));
        document.getElementById('edge-clear').addEventListener('click', () => { source.value = ''; target.value = ''; selectionChanged(); });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape') { cancelHold(); return; }
            if (event.key.toLowerCase() !== 'r' || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.target.closest('input, textarea, select, [contenteditable]')) return;
            if (!holdTimer) holdTimer = setTimeout(() => location.assign('/publish'), Config.ui.creationHoldDuration);
        });
        document.addEventListener('keyup', event => { if (event.key.toLowerCase() === 'r') cancelHold(); });
        window.addEventListener('blur', cancelHold);
        document.addEventListener('visibilitychange', () => { if (document.hidden) cancelHold(); });
    }
    window.addEventListener('pagehide', () => { cancelHold(); disposeGraph(); });
    retry.addEventListener('click', load);
    load();
})();
