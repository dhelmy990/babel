// ============================================
// ANIMATION (Edge Flash)
// ============================================

const Animation = {
    startTime: Date.now(),

    updateEdge(obj, start, end, link) {
        const cfg = Config.edge;
        const animCfg = Config.animation;

        while (obj.children.length > 0) {
            obj.remove(obj.children[0]);
        }

        const startVec = new THREE.Vector3(start.x, start.y, start.z);
        const endVec = new THREE.Vector3(end.x, end.y, end.z);
        const edgeLength = endVec.clone().sub(startVec).length();

        if (edgeLength < 0.1) return;

        const sourceNode = State.getBabel(link.source.id || link.source);
        const targetNode = State.getBabel(link.target.id || link.target);
        const sourceColor = new THREE.Color(sourceNode?.color || '#666666');
        const targetColor = new THREE.Color(targetNode?.color || '#666666');

        const baseGeometry = new THREE.BufferGeometry().setFromPoints([startVec, endVec]);
        const baseMaterial = new THREE.LineBasicMaterial({
            color: cfg.baseColor,
            transparent: true,
            opacity: cfg.baseOpacity
        });
        obj.add(new THREE.Line(baseGeometry, baseMaterial));

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

    startLoop(graph) {
        let lastCircleUpdate = 0;
        const circleInterval = Config.animation.levelCircleUpdateInterval;

        const animate = (timestamp) => {
            requestAnimationFrame(animate);

            const scene = graph.scene();
            if (!scene) return;

            Rendering.updateTime();

            scene.traverse(obj => {
                if (obj.userData?.link) {
                    const { source, target } = obj.userData.link;
                    if (source?.x !== undefined && target?.x !== undefined) {
                        this.updateEdge(obj, source, target, obj.userData.link);
                    }
                }
            });

            if (!LevelCircles.dragState.isDragging && timestamp - lastCircleUpdate > circleInterval) {
                LevelCircles.updateLevelCircles(graph);
                lastCircleUpdate = timestamp;
            }
        };

        requestAnimationFrame(animate);
    }
};
