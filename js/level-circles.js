// ============================================
// LEVEL CIRCLES (DAG Hierarchy Visualization)
// ============================================

const LevelCircles = {
    levelCirclesGroup: null,

    dragState: {
        isDragging: false,
        node: null,
        originalY: null,
        originalX: null,
        originalZ: null,
        levelY: null,
        levelNodes: [],
        originalCenter: null,
    },

    updateLevelCircles(graph) {
        const scene = graph.scene();
        const cfg = Config.levelCircle;

        if (this.levelCirclesGroup) {
            scene.remove(this.levelCirclesGroup);
        }

        this.levelCirclesGroup = new THREE.Group();
        this.levelCirclesGroup.name = 'levelCircles';

        const graphData = graph.graphData();
        if (!graphData.nodes?.length) return;

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

        levelMap.forEach((nodes, levelY) => {
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
            this._drawCircle(centerX, levelY, centerZ, radius, cfg);
        });

        scene.add(this.levelCirclesGroup);
    },

    updateDraggedLevelCircle(graph, overrideX, overrideZ) {
        if (!this.dragState.isDragging) return;

        const scene = graph.scene();
        const cfg = Config.levelCircle;
        const { levelY, levelNodes } = this.dragState;

        if (this.levelCirclesGroup) {
            scene.remove(this.levelCirclesGroup);
        }

        this.levelCirclesGroup = new THREE.Group();
        this.levelCirclesGroup.name = 'levelCircles';

        const graphData = graph.graphData();
        if (!graphData.nodes?.length) return;

        const levelMap = new Map();

        graphData.nodes.forEach(node => {
            if (node.y === undefined) return;
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

        levelMap.forEach((nodes, ly) => {
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

        let centerX = overrideX;
        let centerZ = overrideZ;
        levelNodes.forEach(n => {
            centerX += n.x || 0;
            centerZ += n.z || 0;
        });
        const totalNodes = levelNodes.length + 1;
        centerX /= totalNodes;
        centerZ /= totalNodes;

        let maxDist = 0;

        const draggedDist = Math.sqrt(
            Math.pow(overrideX - centerX, 2) +
            Math.pow(overrideZ - centerZ, 2)
        );
        maxDist = Math.max(maxDist, draggedDist);

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

    startDrag(node, graph) {
        const cfg = Config.levelCircle;
        const graphData = graph.graphData();

        let levelY = node.y;
        let levelNodes = [];

        graphData.nodes.forEach(n => {
            if (n.y === undefined) return;
            if (Math.abs(n.y - node.y) < cfg.tolerance) {
                if (n.id !== node.id) {
                    levelNodes.push(n);
                } else {
                    levelY = n.y;
                }
            }
        });

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
            node,
            originalY: node.y,
            originalX: node.x,
            originalZ: node.z,
            levelY,
            levelNodes,
            originalCenter: { x: centerX, z: centerZ }
        };
    },

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
            const eased = 1 - Math.pow(1 - progress, 3);

            const currentX = startX + (originalX - startX) * eased;
            const currentZ = startZ + (originalZ - startZ) * eased;

            this.updateDraggedLevelCircle(graph, currentX, currentZ);

            if (progress < 1) {
                requestAnimationFrame(animateCircleReturn);
            } else {
                this.dragState.isDragging = false;
                this.dragState.node = null;
                this.updateLevelCircles(graph);
            }
        };

        requestAnimationFrame(animateCircleReturn);
    }
};
