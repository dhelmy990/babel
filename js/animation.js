// ============================================
// ANIMATION (Edge Flash, Level Circles)
// ============================================

const Animation = {
    startTime: Date.now(),
    levelCirclesGroup: null,

    // Drag state for dynamic circle updates
    dragState: {
        isDragging: false,
        node: null,
        originalY: null,
        originalX: null,
        originalZ: null,
        levelY: null,          // The Y level this node belongs to
        levelNodes: [],        // Other nodes at this level (excluding dragged)
        originalCenter: null,  // {x, z} center of level before drag
    },

    /**
     * Update edge with gradient flash animation
     */
    updateEdge(obj, start, end, link) {
        const cfg = Config.edge;
        const animCfg = Config.animation;

        // Clear previous children
        while (obj.children.length > 0) {
            obj.remove(obj.children[0]);
        }

        // Calculate edge vector
        const startVec = new THREE.Vector3(start.x, start.y, start.z);
        const endVec = new THREE.Vector3(end.x, end.y, end.z);
        const edgeLength = endVec.clone().sub(startVec).length();

        if (edgeLength < 0.1) return;

        // Get node colors
        const sourceNode = State.getBabel(link.source.id || link.source);
        const targetNode = State.getBabel(link.target.id || link.target);
        const sourceColor = new THREE.Color(sourceNode?.color || '#666666');
        const targetColor = new THREE.Color(targetNode?.color || '#666666');

        // Base line
        const baseGeometry = new THREE.BufferGeometry().setFromPoints([startVec, endVec]);
        const baseMaterial = new THREE.LineBasicMaterial({
            color: cfg.baseColor,
            transparent: true,
            opacity: cfg.baseOpacity
        });
        obj.add(new THREE.Line(baseGeometry, baseMaterial));

        // Flash animation
        const elapsed = (Date.now() - this.startTime) % animCfg.edgeFlashDuration;
        const flashProgress = elapsed / animCfg.edgeFlashDuration;
        const flashLength = animCfg.edgeFlashLength;
        const flashStart = flashProgress - flashLength / 2;
        const flashEnd = flashProgress + flashLength / 2;

        if (flashEnd > 0 && flashStart < 1) {
            const clampedStart = Math.max(0, flashStart);
            const clampedEnd = Math.min(1, flashEnd);
            const numSegments = 10;
            const positions = [];
            const colors = [];

            for (let i = 0; i <= numSegments; i++) {
                const t = clampedStart + (clampedEnd - clampedStart) * (i / numSegments);
                const pos = startVec.clone().lerp(endVec, t);
                positions.push(pos.x, pos.y, pos.z);

                const color = sourceColor.clone().lerp(targetColor, t);
                colors.push(color.r, color.g, color.b);
            }

            const flashGeometry = new THREE.BufferGeometry();
            flashGeometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
            flashGeometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

            const flashMaterial = new THREE.LineBasicMaterial({
                vertexColors: true,
                transparent: true,
                opacity: cfg.flashOpacity,
                linewidth: 2
            });

            obj.add(new THREE.Line(flashGeometry, flashMaterial));
        }
    },

    /**
     * Update level circles for DAG hierarchy
     */
    updateLevelCircles(graph) {
        const scene = graph.scene();
        const cfg = Config.levelCircle;

        // Remove old circles
        if (this.levelCirclesGroup) {
            scene.remove(this.levelCirclesGroup);
        }

        this.levelCirclesGroup = new THREE.Group();
        this.levelCirclesGroup.name = 'levelCircles';

        const graphData = graph.graphData();
        if (!graphData.nodes?.length) return;

        // Group nodes by Y level
        const levelMap = new Map();

        graphData.nodes.forEach(node => {
            if (node.y === undefined) return;

            let foundLevel = null;
            for (const [levelY] of levelMap) {
                if (Math.abs(node.y - levelY) < cfg.tolerance) {
                    foundLevel = levelY;
                    break;
                }
            }

            if (foundLevel !== null) {
                levelMap.get(foundLevel).push(node);
            } else {
                levelMap.set(node.y, [node]);
            }
        });

        // Draw circle for each level
        levelMap.forEach((nodes, levelY) => {
            if (nodes.length < 1) return;

            // Calculate center
            let centerX = 0, centerZ = 0;
            nodes.forEach(node => {
                centerX += node.x || 0;
                centerZ += node.z || 0;
            });
            centerX /= nodes.length;
            centerZ /= nodes.length;

            // Calculate radius
            let maxDist = 0;
            nodes.forEach(node => {
                const dist = Math.sqrt(
                    Math.pow((node.x || 0) - centerX, 2) +
                    Math.pow((node.z || 0) - centerZ, 2)
                );
                maxDist = Math.max(maxDist, dist);
            });

            const radius = Math.max(maxDist + cfg.padding, cfg.minRadius);

            // Create circle geometry
            const positions = [];
            for (let i = 0; i <= cfg.segments; i++) {
                const theta = (i / cfg.segments) * Math.PI * 2;
                positions.push(
                    centerX + Math.cos(theta) * radius,
                    levelY,
                    centerZ + Math.sin(theta) * radius
                );
            }

            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));

            const material = new THREE.LineDashedMaterial({
                color: 0xffffff,
                transparent: true,
                opacity: cfg.opacity,
                dashSize: cfg.dashSize,
                gapSize: cfg.gapSize
            });

            const circle = new THREE.Line(geometry, material);
            circle.computeLineDistances();
            this.levelCirclesGroup.add(circle);
        });

        scene.add(this.levelCirclesGroup);
    },

    /**
     * Start tracking a node drag for dynamic circle updates
     */
    startDrag(node, graph) {
        console.log('startDrag called for node:', node.id, 'at position:', node.x, node.y, node.z);
        const cfg = Config.levelCircle;
        const graphData = graph.graphData();

        // Find which level this node belongs to
        let levelY = node.y;
        let levelNodes = [];

        // Group all nodes by Y level to find siblings
        graphData.nodes.forEach(n => {
            if (n.y === undefined) return;
            if (Math.abs(n.y - node.y) < cfg.tolerance) {
                if (n.id !== node.id) {
                    levelNodes.push(n);
                } else {
                    levelY = n.y; // Use exact Y of dragged node's level
                }
            }
        });

        // Calculate original center (including dragged node)
        let centerX = node.x || 0;
        let centerZ = node.z || 0;
        levelNodes.forEach(n => {
            centerX += n.x || 0;
            centerZ += n.z || 0;
        });
        const totalNodes = levelNodes.length + 1;
        centerX /= totalNodes;
        centerZ /= totalNodes;

        this.dragState = {
            isDragging: true,
            node: node,
            originalY: node.y,
            originalX: node.x,
            originalZ: node.z,
            levelY: levelY,
            levelNodes: levelNodes,
            originalCenter: { x: centerX, z: centerZ }
        };
    },

    /**
     * Update the dragged node's level circle with current position
     */
    updateDraggedLevelCircle(graph, overrideX, overrideZ) {
        console.log('updateDraggedLevelCircle called, isDragging:', this.dragState.isDragging, 'override:', overrideX, overrideZ);
        if (!this.dragState.isDragging) return;

        const scene = graph.scene();
        const cfg = Config.levelCircle;
        const { levelY, levelNodes, originalCenter } = this.dragState;

        // Remove old circles and recreate
        if (this.levelCirclesGroup) {
            scene.remove(this.levelCirclesGroup);
        }

        this.levelCirclesGroup = new THREE.Group();
        this.levelCirclesGroup.name = 'levelCircles';

        const graphData = graph.graphData();
        if (!graphData.nodes?.length) return;

        // Group nodes by Y level
        const levelMap = new Map();

        graphData.nodes.forEach(node => {
            if (node.y === undefined) return;

            // Skip the dragged node - we'll handle it specially
            if (node.id === this.dragState.node.id) return;

            let foundLevel = null;
            for (const [ly] of levelMap) {
                if (Math.abs(node.y - ly) < cfg.tolerance) {
                    foundLevel = ly;
                    break;
                }
            }

            if (foundLevel !== null) {
                levelMap.get(foundLevel).push(node);
            } else {
                levelMap.set(node.y, [node]);
            }
        });

        // Draw circles for non-dragged levels normally
        levelMap.forEach((nodes, ly) => {
            // Skip the dragged node's level - handle separately
            if (Math.abs(ly - levelY) < cfg.tolerance) return;

            if (nodes.length < 1) return;

            let centerX = 0, centerZ = 0;
            nodes.forEach(node => {
                centerX += node.x || 0;
                centerZ += node.z || 0;
            });
            centerX /= nodes.length;
            centerZ /= nodes.length;

            let maxDist = 0;
            nodes.forEach(node => {
                const dist = Math.sqrt(
                    Math.pow((node.x || 0) - centerX, 2) +
                    Math.pow((node.z || 0) - centerZ, 2)
                );
                maxDist = Math.max(maxDist, dist);
            });

            const radius = Math.max(maxDist + cfg.padding, cfg.minRadius);
            this._drawCircle(centerX, ly, centerZ, radius, cfg);
        });

        // Now draw the dragged level's circle with the override position
        // Center is calculated from siblings + dragged node's current position
        let centerX = overrideX;
        let centerZ = overrideZ;
        levelNodes.forEach(n => {
            centerX += n.x || 0;
            centerZ += n.z || 0;
        });
        const totalNodes = levelNodes.length + 1;
        centerX /= totalNodes;
        centerZ /= totalNodes;

        // Radius must include all siblings AND the dragged node at override position
        let maxDist = 0;

        // Distance from dragged node to center
        const draggedDist = Math.sqrt(
            Math.pow(overrideX - centerX, 2) +
            Math.pow(overrideZ - centerZ, 2)
        );
        maxDist = Math.max(maxDist, draggedDist);

        // Distance from siblings to center
        levelNodes.forEach(n => {
            const dist = Math.sqrt(
                Math.pow((n.x || 0) - centerX, 2) +
                Math.pow((n.z || 0) - centerZ, 2)
            );
            maxDist = Math.max(maxDist, dist);
        });

        const radius = Math.max(maxDist + cfg.padding, cfg.minRadius);
        this._drawCircle(centerX, levelY, centerZ, radius, cfg);

        scene.add(this.levelCirclesGroup);
    },

    /**
     * Helper to draw a single dashed circle
     */
    _drawCircle(centerX, levelY, centerZ, radius, cfg) {
        const positions = [];
        for (let i = 0; i <= cfg.segments; i++) {
            const theta = (i / cfg.segments) * Math.PI * 2;
            positions.push(
                centerX + Math.cos(theta) * radius,
                levelY,
                centerZ + Math.sin(theta) * radius
            );
        }

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));

        const material = new THREE.LineDashedMaterial({
            color: 0xffffff,
            transparent: true,
            opacity: cfg.opacity,
            dashSize: cfg.dashSize,
            gapSize: cfg.gapSize
        });

        const circle = new THREE.Line(geometry, material);
        circle.computeLineDistances();
        this.levelCirclesGroup.add(circle);
    },

    /**
     * End drag and animate circle back during node return
     */
    endDrag(graph) {
        if (!this.dragState.isDragging) return;

        const { node, originalX, originalZ } = this.dragState;
        const startX = node.x;
        const startZ = node.z;
        const startTime = Date.now();
        const duration = Config.animation.dragReturnDuration;

        const animateCircleReturn = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic

            // Interpolate position
            const currentX = startX + (originalX - startX) * eased;
            const currentZ = startZ + (originalZ - startZ) * eased;

            this.updateDraggedLevelCircle(graph, currentX, currentZ);

            if (progress < 1) {
                requestAnimationFrame(animateCircleReturn);
            } else {
                // Done - clear drag state and do full update
                this.dragState.isDragging = false;
                this.dragState.node = null;
                this.updateLevelCircles(graph);
            }
        };

        requestAnimationFrame(animateCircleReturn);
    },

    /**
     * Start continuous animation loop
     */
    startLoop(graph) {
        let lastCircleUpdate = 0;
        const circleInterval = Config.animation.levelCircleUpdateInterval;

        const animate = (timestamp) => {
            requestAnimationFrame(animate);

            const scene = graph.scene();
            if (!scene) return;

            // Update hole effect animation (jittery shader)
            Rendering.updateTime();

            // Update edge animations
            scene.traverse(obj => {
                if (obj.userData?.link) {
                    const { source, target } = obj.userData.link;
                    if (source?.x !== undefined && target?.x !== undefined) {
                        this.updateEdge(obj, source, target, obj.userData.link);
                    }
                }
            });

            // Periodic level circle updates (skip during drag - drag handles its own updates)
            if (!this.dragState.isDragging && timestamp - lastCircleUpdate > circleInterval) {
                this.updateLevelCircles(graph);
                lastCircleUpdate = timestamp;
            }
        };

        requestAnimationFrame(animate);
    }
};
