// ============================================
// CONFIGURATION
// ============================================

const Config = {
    // Color presets
    colors: {
        bright: [
            '#8B0000', '#006666', '#1a5276', '#1e8449', '#7d6608',
            '#6c3483', '#117a65', '#7e5109', '#4a235a', '#1b4f72'
        ],
        dark: [
            '#8B0000', '#006666', '#1a5276', '#1e8449', '#7d6608',
            '#6c3483', '#117a65', '#7e5109', '#4a235a', '#1b4f72'
        ]
    },

    // Graph settings
    graph: {
        backgroundColor: '#0a0a0a',
        dagMode: 'bu',
        dagLevelDistance: 40,
        defaultNodeColor: '#1a5276'
    },

    // Physics settings
    physics: {
        alphaDecay: 0.05,
        velocityDecay: 0.4,
        chargeStrength: -30,
        chargeDistanceMax: 100,
        linkStrength: 0.2
    },

    // Animation settings
    animation: {
        edgeFlashDuration: 2000,      // 2 seconds per flash cycle
        edgeFlashLength: 0.3,          // Flash covers 30% of edge
        dragReturnDuration: 500        // 500ms to return after drag
    },

    // Node rendering
    node: {
        radius: 5,
        segments: 64,
        glowRadius: 5.5,
        glowOpacity: 0.15,
        selectionRingRadius: 7,
        selectionRingTube: 0.4
    },

    // Edge rendering
    edge: {
        baseColor: 0x444444,
        baseOpacity: 0.4,
        flashOpacity: 0.9
    },

    // Level circles
    levelCircle: {
        segments: 64,
        opacity: 0.2,
        dashSize: 3,
        gapSize: 2,
        padding: 15,
        minRadius: 20,
        tolerance: 5  // Y positions within this range are same level
    },

    // Lighting
    lighting: {
        ambient: { color: 0x404040, intensity: 0.5 },
        main: { color: 0xffffff, intensity: 1.0, position: [100, 100, 100] },
        fill: { color: 0x6688cc, intensity: 0.4, position: [-100, -50, -100] },
        point: { color: 0xffffff, intensity: 0.3, distance: 500, position: [0, 0, 200] }
    },

    // UI timing
    ui: {
        creationHoldDuration: 2000,
        deleteWarningDuration: 2000,
        toastDuration: 2000,
        panelAnimationDelay: 100,
        panelCloseDelay: 300,
        doubleClickThreshold: 300
    },

    // Persistence
    storage: {
        key: 'babel-graph'
    }
};

// Freeze config to prevent accidental modification
for (const key of Object.keys(Config)) {
    if (typeof Config[key] === 'object' && Config[key] !== null) {
        Object.freeze(Config[key]);
    }
}
Object.freeze(Config); // Don't forget to freeze the top-level parent too!
