// ============================================
// RENDERING (Textures, Materials, Lighting)
// ============================================

// Vertex shader - passes UV and normal data to fragment shader
const HOLE_VERTEX_SHADER = `
varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vWorldPosition;

void main() {
    vUv = uv;
    vNormal = normalize(normalMatrix * normal);
    vWorldPosition = (modelMatrix * vec4(position, 1.0)).xyz;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

// Fragment shader - creates the murky hole effect
const HOLE_FRAGMENT_SHADER = `
uniform sampler2D planetTexture;
uniform vec2 holeCenter;
uniform float holeRadius;
uniform float time;
uniform bool isHovering;
uniform vec3 emissiveColor;
uniform float emissiveIntensity;

varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vWorldPosition;

void main() {
    vec4 texColor = texture2D(planetTexture, vUv);

    if (isHovering) {
        // Calculate distance to hole center in UV space
        vec2 diff = vUv - holeCenter;

        // Handle UV wrapping (texture repeats)
        if (diff.x > 0.5) diff.x -= 1.0;
        if (diff.x < -0.5) diff.x += 1.0;

        float dist = length(diff);

        // Jittery edge using multiple sin waves at different frequencies
        float jitter = 0.015 * sin(time * 7.0 + vUv.x * 30.0)
                     + 0.01 * sin(time * 11.0 + vUv.y * 25.0)
                     + 0.008 * sin(time * 5.0 + (vUv.x + vUv.y) * 40.0);

        float edgeRadius = holeRadius + jitter;

        // Murky hole effect - darker towards center with soft edge
        float holeFactor = smoothstep(edgeRadius, edgeRadius * 0.3, dist);

        // Add some swirling darkness variation inside the hole
        float swirl = 0.1 * sin(time * 3.0 + dist * 50.0);
        float darkness = 0.95 + swirl * holeFactor;

        texColor.rgb = mix(texColor.rgb, vec3(0.02, 0.01, 0.03), holeFactor * darkness);
    }

    // Basic lighting calculation
    vec3 lightDir = normalize(vec3(1.0, 1.0, 1.0));
    float diff = max(dot(vNormal, lightDir), 0.0);

    vec3 ambient = 0.4 * texColor.rgb;
    vec3 diffuse = 0.6 * diff * texColor.rgb;
    vec3 emissive = emissiveColor * emissiveIntensity;

    gl_FragColor = vec4(ambient + diffuse + emissive, 1.0);
}
`;

const Rendering = {
    textureCache: new Map(),

    // Track hovered node's material for updates
    hoveredMaterial: null,
    hoverStartTime: 0,

    /**
     * Generate Neptune-like procedural texture
     */
    createPlanetTexture(baseColor, size = 256) {
        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext('2d');

        const color = new THREE.Color(baseColor);
        const hsl = {};
        color.getHSL(hsl);

        // Base color
        ctx.fillStyle = baseColor;
        ctx.fillRect(0, 0, size, size);

        // Atmospheric bands
        for (let i = 0; i < 12; i++) {
            const y = (i / 12) * size;
            const bandHeight = size / 12 + Math.random() * 10;
            const bandLightness = hsl.l + (Math.random() - 0.5) * 0.3;
            const bandColor = new THREE.Color().setHSL(
                hsl.h, hsl.s * 0.8,
                Math.max(0.1, Math.min(0.9, bandLightness))
            );

            ctx.fillStyle = '#' + bandColor.getHexString();
            ctx.globalAlpha = 0.3 + Math.random() * 0.4;
            ctx.fillRect(0, y, size, bandHeight);
        }

        // Turbulence noise
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

        // Storm spots
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
    },

    /**
     * Get cached planet texture
     */
    getPlanetTexture(color) {
        if (!this.textureCache.has(color)) {
            this.textureCache.set(color, this.createPlanetTexture(color));
        }
        return this.textureCache.get(color);
    },

    /**
     * Create shader material with hole effect
     */
    createHoleMaterial(texture, color) {
        return new THREE.ShaderMaterial({
            uniforms: {
                planetTexture: { value: texture },
                holeCenter: { value: new THREE.Vector2(0.5, 0.5) },
                holeRadius: { value: 0.08 },
                time: { value: 0 },
                isHovering: { value: false },
                emissiveColor: { value: new THREE.Color(color) },
                emissiveIntensity: { value: 0.15 }
            },
            vertexShader: HOLE_VERTEX_SHADER,
            fragmentShader: HOLE_FRAGMENT_SHADER
        });
    },

    /**
     * Convert 3D point on sphere to UV coordinates
     */
    pointToUV(localPoint, radius) {
        // Normalize the point to unit sphere
        const normalized = localPoint.clone().normalize();

        // Spherical coordinates to UV
        // u = 0.5 + atan2(z, x) / (2*PI)
        // v = 0.5 - asin(y) / PI
        const u = 0.5 + Math.atan2(normalized.z, normalized.x) / (2 * Math.PI);
        const v = 0.5 - Math.asin(Math.max(-1, Math.min(1, normalized.y))) / Math.PI;

        return new THREE.Vector2(u, v);
    },

    /**
     * Update hole position on hovered material
     */
    updateHolePosition(uv) {
        if (this.hoveredMaterial) {
            this.hoveredMaterial.uniforms.holeCenter.value.copy(uv);
        }
    },

    /**
     * Set hover state for a material
     */
    setHoverState(material, isHovering) {
        if (material && material.uniforms) {
            material.uniforms.isHovering.value = isHovering;
            if (isHovering) {
                this.hoveredMaterial = material;
                this.hoverStartTime = performance.now();
            } else if (this.hoveredMaterial === material) {
                this.hoveredMaterial = null;
            }
        }
    },

    /**
     * Update time uniform for animation
     */
    updateTime() {
        if (this.hoveredMaterial) {
            const elapsed = (performance.now() - this.hoverStartTime) / 1000;
            this.hoveredMaterial.uniforms.time.value = elapsed;
        }
    },

    /**
     * Create node Three.js object.
     * Callers compute isSelected/isDeleteWarning from State so Rendering stays pure.
     */
    createNodeObject(node, { isSelected = false, isDeleteWarning = false } = {}) {
        const group = new THREE.Group();
        const cfg = Config.node;

        const color = isDeleteWarning
            ? '#ff0000'
            : (node.color || Config.graph.defaultNodeColor);

        const geometry = new THREE.SphereGeometry(cfg.radius, cfg.segments, cfg.segments);
        const texture = this.getPlanetTexture(color);
        const material = this.createHoleMaterial(texture, color);

        const sphere = new THREE.Mesh(geometry, material);
        sphere.rotation.z = Math.random() * 0.3;
        sphere.userData.isMainSphere = true;
        sphere.userData.nodeId = node.id;
        group.add(sphere);

        const glowGeometry = new THREE.SphereGeometry(cfg.glowRadius, 32, 32);
        const glowMaterial = new THREE.MeshBasicMaterial({
            color,
            transparent: true,
            opacity: cfg.glowOpacity,
            side: THREE.BackSide
        });
        group.add(new THREE.Mesh(glowGeometry, glowMaterial));

        if (isSelected) {
            const ringGeometry = new THREE.TorusGeometry(
                cfg.selectionRingRadius, cfg.selectionRingTube, 16, 100
            );
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
    },

    /**
     * Create a self-contained 3D preview inside container.
     * Returns a handle with updateColor(color) and destroy().
     */
    createPreview(container, color) {
        const scene = new THREE.Scene();

        const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
        camera.position.z = 10;

        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setClearColor(0x000000, 0);
        renderer.setPixelRatio(window.devicePixelRatio);
        container.innerHTML = '';
        container.appendChild(renderer.domElement);

        // Keep renderer exactly in sync with container size
        const ro = new ResizeObserver(entries => {
            const { width, height } = entries[0].contentRect;
            if (width === 0 || height === 0) return;
            renderer.setSize(width, height);
            camera.aspect = width / height;
            camera.updateProjectionMatrix();
        });
        ro.observe(container);

        // Seed initial size
        const w = container.clientWidth || 400;
        const h = container.clientHeight || 400;
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();

        const geometry = new THREE.SphereGeometry(4, 64, 64);
        const texture = this.createPlanetTexture(color);
        const material = new THREE.MeshStandardMaterial({ map: texture });
        const sphere = new THREE.Mesh(geometry, material);
        scene.add(sphere);

        scene.add(new THREE.AmbientLight(0x404040, 0.6));
        const mainLight = new THREE.DirectionalLight(0xffffff, 1.0);
        mainLight.position.set(5, 5, 5);
        scene.add(mainLight);
        const fillLight = new THREE.DirectionalLight(0x6688cc, 0.4);
        fillLight.position.set(-5, -3, -5);
        scene.add(fillLight);

        let animId = null;
        const tick = () => {
            animId = requestAnimationFrame(tick);
            sphere.rotation.y += 0.005;
            renderer.render(scene, camera);
        };
        tick();

        return {
            updateColor: (newColor) => {
                sphere.material.map = this.createPlanetTexture(newColor);
                sphere.material.needsUpdate = true;
            },
            destroy: () => {
                if (animId) cancelAnimationFrame(animId);
                ro.disconnect();
                renderer.dispose();
                sphere.geometry.dispose();
                sphere.material.dispose();
            }
        };
    },

    /**
     * Setup scene lighting
     */
    setupLighting(scene) {
        const cfg = Config.lighting;

        // Remove existing lights
        scene.children = scene.children.filter(child => !(child instanceof THREE.Light));

        // Ambient
        scene.add(new THREE.AmbientLight(cfg.ambient.color, cfg.ambient.intensity));

        // Main directional
        const mainLight = new THREE.DirectionalLight(cfg.main.color, cfg.main.intensity);
        mainLight.position.set(...cfg.main.position);
        scene.add(mainLight);

        // Fill directional
        const fillLight = new THREE.DirectionalLight(cfg.fill.color, cfg.fill.intensity);
        fillLight.position.set(...cfg.fill.position);
        scene.add(fillLight);

        // Point light
        const pointLight = new THREE.PointLight(
            cfg.point.color, cfg.point.intensity, cfg.point.distance
        );
        pointLight.position.set(...cfg.point.position);
        scene.add(pointLight);
    }
};
