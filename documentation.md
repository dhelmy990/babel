# Babel — Codebase Documentation

## What it is

Babel is a browser-based 3D knowledge graph editor. Nodes ("babels") are planet-like spheres rendered in Three.js and arranged as a directed acyclic graph (DAG) by a force-simulation layout. Edges represent relationships between babels. Each babel has a rich-text body edited with Quill.

Run it with:
```
python3 -m http.server 3000
# then open http://localhost:3000/
```

---

## File map

```
index.html          Entry point — loads scripts in dependency order
styles.css          All CSS
app.js              Root-level app.js (legacy, now empty — see js/app.js)
js/
  config.js         Frozen configuration constants
  state.js          Global mutable data + mutation methods
  graph-utils.js    Pure DAG algorithms (cycle detection, transitivity)
  rendering.js      Three.js materials, shaders, textures, lighting, preview
  level-circles.js  DAG hierarchy circles + drag animation
  animation.js      Edge flash animation + main render loop
  persistence.js    localStorage save/load, JSON export/import
  ui.js             DOM wiring, overlays, Quill editor, CustomEvent dispatch
  app.js            Graph init, node interaction handlers, event listeners
```

Scripts load in this exact order (each can rely on everything before it):

```
config → state → graph-utils → rendering → level-circles → animation → persistence → ui → app
```

---

## Module reference

### `Config` (`js/config.js`)

A frozen plain object. All magic numbers live here — colors, physics constants, timing, node sizes, edge appearance, lighting. Never written to at runtime.

Key sections:

| Key | Purpose |
|-----|---------|
| `Config.colors.bright` | 10 color presets for babels |
| `Config.graph` | Background color, DAG mode, level distance |
| `Config.physics` | Force-simulation tuning |
| `Config.animation` | Edge flash timing, circle update interval |
| `Config.node` | Sphere radius, glow, selection ring dimensions |
| `Config.edge` | Base line color/opacity, flash opacity |
| `Config.levelCircle` | Dashed circle rendering, tolerance for Y grouping |
| `Config.lighting` | Ambient, main, fill, point light settings |
| `Config.ui` | Hold duration, toast duration, double-click threshold |
| `Config.storage` | localStorage key |

---

### `State` (`js/state.js`)

The single source of truth for all runtime data. Modules read and mutate State — it is a mutable global singleton, not a reactive store.

**Data:**

```js
State.babels        // Array of babel objects: { id, title, description, color }
State.edges         // Array of edge objects: { id, source, target }
```

**Interaction state:**

```js
State.selectedBabel         // Currently selected babel (or null)
State.comparisonBabels      // Array of 0–2 babels being compared
State.editingBabel          // Babel open in the rich-text editor
State.isCreating            // Whether creation hold is in progress
State.selectedColor         // Color chosen in the creation/edit palette
State.selectedSimilarBabels // IDs chosen in the creation form
State.deleteWarningBabel    // Babel currently in delete-confirm state
State.deleteWarningTimeout  // setTimeout handle for delete warning
State.isPhysicsPaused       // (reserved)
```

**Methods:**

| Method | What it does |
|--------|-------------|
| `State.reset()` | Wipes everything back to initial values |
| `State.addBabel(babel)` | Appends to `babels` |
| `State.removeBabel(id)` | Removes babel and all its edges |
| `State.getBabel(id)` | Returns babel by id |
| `State.addEdge(source, target)` | Adds edge if not already present; returns bool |
| `State.removeEdge(source, target)` | Removes edge; returns bool |
| `State.hasEdge(source, target)` | Checks existence |
| `State.getValidEdges()` | Filters out edges whose nodes no longer exist |

---

### `GraphUtils` (`js/graph-utils.js`)

Pure DAG algorithms. **None of these functions mutate State** — callers receive arrays of edges to remove and apply them via `State.removeEdge`.

| Method | Signature | What it does |
|--------|-----------|-------------|
| `getDependencyEdges` | `(edges)` | Returns only one-directional edges (filters out mutual/association edges) |
| `buildAdjacencyList` | `(edges, excludeEdge?)` | Returns a `Map<id, id[]>` adjacency list |
| `hasPath` | `(sourceId, targetId, edges, excludeDirectEdge?)` | BFS reachability check |
| `wouldCreateCycle` | `(sourceId, targetId, edges)` | Returns true if adding this edge would form a cycle |
| `pruneTransitiveEdges` | `(edges)` | Returns edges that are redundant (path exists without them) |
| `breakCycles` | `(edges)` | Returns edges whose removal makes the graph acyclic |
| `cleanGraphOnLoad` | `()` | Runs break+prune, applies results to State — called once on startup |
| `identifyMutualEdges` | `(edges)` | Stamps `.isMutual = true` on bidirectional pairs |
| `getDagEdges` | `(edges)` | Filters mutual edges, returning only DAG-compatible edges for layout |

**Edge types:**
- **Dependency edge** — one direction only: A → B but not B → A. Participates in DAG layout.
- **Mutual/association edge** — A → B and B → A both exist. Rendered as a link but excluded from DAG layout (would break the acyclic constraint).

---

### `Rendering` (`js/rendering.js`)

Owns all Three.js object construction: procedural planet textures, custom GLSL shaders, node meshes, scene lighting, and the edit-mode 3D preview. **Does not read State** — callers pass in all the data Rendering needs.

**Shaders:**

Two inline GLSL strings — `HOLE_VERTEX_SHADER` and `HOLE_FRAGMENT_SHADER`. The fragment shader renders a "murky hole" effect on the hovered babel's sphere: a jittery dark vortex that follows the mouse cursor's UV position.

**Key methods:**

| Method | What it does |
|--------|-------------|
| `createPlanetTexture(color, size?)` | Generates a procedural Neptune-like texture on a `<canvas>`. Atmospheric bands, turbulence noise, storm spots. |
| `getPlanetTexture(color)` | Cache wrapper around `createPlanetTexture`. One texture per color hex string. |
| `createHoleMaterial(texture, color)` | Returns a `THREE.ShaderMaterial` with the hole/hover uniforms. |
| `pointToUV(localPoint, radius)` | Converts a 3D intersection point on a sphere to UV coordinates for the hole shader. |
| `updateHolePosition(uv)` | Pushes a new UV into the hovered material's `holeCenter` uniform. |
| `setHoverState(material, isHovering)` | Toggles the hole effect on/off; tracks `hoveredMaterial` and `hoverStartTime`. |
| `updateTime()` | Advances the `time` uniform on the hovered material — called every frame. |
| `createNodeObject(node, { isSelected, isDeleteWarning })` | Builds the full Three.js `Group` for a babel node: main sphere + glow sphere + optional selection ring. Flags are passed by the caller; Rendering does not read State. |
| `setupLighting(scene)` | Adds ambient, main directional, fill directional, and point lights to the scene. |
| `createPreview(container, color)` | Sets up a self-contained Three.js preview (scene, camera, renderer, lighting, rotation loop) inside `container`. Returns a handle: `{ updateColor(color), destroy() }`. |

**Texture cache:** `Rendering.textureCache` is a `Map`. It is cleared in `UI.closeEdit()` so that color changes in the editor are immediately reflected in the main graph on the next render pass.

---

### `LevelCircles` (`js/level-circles.js`)

Manages the dashed concentric circles that visualize DAG hierarchy levels. Also owns all drag-animation state so that circles update smoothly while a node is being dragged.

**State:**

```js
LevelCircles.levelCirclesGroup  // THREE.Group currently in the scene
LevelCircles.dragState          // Drag tracking: isDragging, node, levelY, levelNodes, originalCenter, …
```

**Methods:**

| Method | What it does |
|--------|-------------|
| `updateLevelCircles(graph)` | Groups all nodes by Y position (within `Config.levelCircle.tolerance`), computes the bounding circle for each group, redraws. Called periodically from the animation loop and after graph updates. |
| `updateDraggedLevelCircle(graph, overrideX, overrideZ)` | Same as above but substitutes the dragged node's current XZ position instead of its graph position. Called every drag frame. |
| `_drawCircle(centerX, levelY, centerZ, radius, cfg)` | Appends a single dashed `THREE.Line` circle to `levelCirclesGroup`. |
| `startDrag(node, graph)` | Captures drag state: level Y, sibling nodes, original center. |
| `endDrag(graph)` | Animates the circle back to its settled position over `Config.animation.dragReturnDuration` ms, then calls `updateLevelCircles`. |

---

### `Animation` (`js/animation.js`)

Owns the edge flash animation and the main `requestAnimationFrame` loop. Level-circle logic was separated into `LevelCircles`.

**Methods:**

| Method | What it does |
|--------|-------------|
| `updateEdge(obj, start, end, link)` | Rebuilds the Three.js geometry for one edge every frame: a dim base line plus a short moving gradient flash whose progress is derived from `Date.now()`. |
| `startLoop(graph)` | Starts the RAF loop. Each tick: advances the hole shader time uniform, redraws all edges, and periodically calls `LevelCircles.updateLevelCircles` (every `Config.animation.levelCircleUpdateInterval` ms, skipped during drag). |

---

### `Persistence` (`js/persistence.js`)

Serializes and deserializes the graph to/from `localStorage` under `Config.storage.key`.

| Method | What it does |
|--------|-------------|
| `save()` | JSON-serializes `State.babels` and `State.edges` to localStorage. |
| `load()` | Reads and parses localStorage, writes into `State.babels` / `State.edges`. |
| `exportJSON()` | Downloads the graph as a `.json` file via a temporary anchor element. |
| `importJSON(file)` | Reads a `File` object, parses JSON, writes into State. Returns a Promise. |
| `clear()` | Removes the localStorage entry and calls `State.reset()`. |

---

### `UI` (`js/ui.js`)

Owns all DOM interaction: overlay lifecycle, color palette, Quill editor initialization, the 3D edit-mode preview, and keyboard/button event handling. Communicates back to `app.js` exclusively via `CustomEvent`s dispatched on `document`.

**Events fired by UI:**

| Event | When |
|-------|------|
| `babel:create` | Create button clicked or Enter pressed in creation form |
| `babel:toggle-edge` | Edge arrow clicked in comparison overlay (`detail.direction`: `'left-to-right'` or `'right-to-left'`) |
| `babel:deselect` | Escape pressed with a selected babel but no active overlay |
| `babel:delete` | Delete/Backspace pressed with a selected babel |
| `babel:reset-camera` | Space pressed |
| `babel:save` | Ctrl+S pressed |
| `babel:comparison-closed` | Comparison overlay animation finishes closing |
| `babel:edit-closed` | Edit overlay closes |

**Key methods:**

| Method | What it does |
|--------|-------------|
| `init()` | Caches all DOM element references into `UI.elements`. |
| `setupColorPalette()` | Populates both the creation-form and edit-mode color swatches from `Config.colors.bright`. |
| `setupEventListeners()` | Wires all buttons and global key handlers. Fires CustomEvents for app-level actions. |
| `startCreation()` / `cancelCreation()` | Shows/hides the creation hold animation. |
| `showCreationForm()` | Transitions from hold animation into the creation form panel. |
| `openComparison()` | Populates and shows the comparison overlay for `State.comparisonBabels[0]` and `[1]`. |
| `closeComparison()` | Saves inline edits, animates panels closed, fires `babel:comparison-closed`. |
| `openEdit(babel)` | Sets `State.editingBabel`, loads content into Quill and title field, shows edit overlay, initializes 3D preview. |
| `closeEdit()` | Saves babel, cleans up preview, fires `babel:edit-closed`. |
| `initEditor()` | Registers custom Quill blots (highlight, youtube, pdf) once, then creates the Quill instance. |
| `initPreview(color)` | Delegates to `Rendering.createPreview`; stores the returned handle in `UI.preview`. |
| `updatePreviewColor(color)` | Calls `UI.preview.updateColor(color)`. |
| `cleanupPreview()` | Calls `UI.preview.destroy()`. |
| `triggerAutoSave()` | Debounces `saveCurrentBabel()` by 2 seconds. |
| `saveCurrentBabel()` | Reads title, Quill HTML, and selected color into `State.editingBabel`, then calls `Persistence.save()`. |

**Quill custom blots:**
- `HighlightBlot` — `<mark>` with yellow background
- `YouTubeBlot` — inline pill that copies URL to clipboard on click
- `PDFBlot` — inline pill that opens the file URL in a new tab on click

**Babel mention (`@`):** Partially implemented. Typing `@` in the editor detects the trigger and calls `showBabelSelector`, which currently returns an empty recommendation list.

---

### `app.js` (`js/app.js`)

Application entry point and coordinator. Initializes the graph, wires up 3d-force-graph event handlers, listens for CustomEvents from UI, and owns functions that require access to the `Graph` object.

**Startup sequence (`init`):**
1. `UI.init()` — cache DOM
2. `initGraph()` — create ForceGraph3D instance
3. `Rendering.setupLighting(scene)` — add lights
4. `Persistence.load()` — restore saved data
5. `GraphUtils.cleanGraphOnLoad()` — fix any corrupted edges
6. `UI.setupColorPalette()` — populate swatches
7. `UI.setupEventListeners()` — wire DOM
8. Register CustomEvent listeners on `document`
9. `updateGraph()` — push data into the graph library
10. `Animation.startLoop(Graph)` — begin RAF loop
11. Register `mousemove` / `mouseleave` listeners for the hole raycaster

**ForceGraph3D callbacks:**

| Callback | Handler | Notes |
|----------|---------|-------|
| `nodeThreeObject` | `Rendering.createNodeObject(node, flags)` | Flags computed from State at call time |
| `linkThreeObject` | Creates a `THREE.Group` tagged with the link | Geometry filled in by `linkPositionUpdate` |
| `linkPositionUpdate` | `Animation.updateEdge(...)` | Redraws edge geometry each frame |
| `onNodeClick` | `handleNodeClick` | Single click = select; double-click = focus camera |
| `onNodeRightClick` | `handleNodeRightClick` | First right-click stages for comparison; second opens comparison overlay |
| `onNodeHover` | `handleNodeHover` | Tracks which node the cursor is over for the raycaster |
| `onBackgroundClick` | `handleBackgroundClick` | Deselects and clears comparison staging |
| `onNodeDrag` | `handleNodeDrag` | Fixes node position, delegates to `LevelCircles` |
| `onNodeDragEnd` | `handleNodeDragEnd` | Releases fix, animates Y back to DAG level, ends LevelCircles drag |
| `onEngineStop` | `LevelCircles.updateLevelCircles` | Redraws circles once physics settle |

**Hover raycasting:** `handleMouseMove` runs every `mousemove` event on the graph container. It uses `THREE.Raycaster` to find the first intersected mesh tagged `isMainSphere`, then converts the 3D hit point to UV and passes it to `Rendering.updateHolePosition`. This drives the hole shader on the hovered babel.

**`updateGraph()`:** Calls `State.getValidEdges()`, then `GraphUtils.getDagEdges()` to strip mutual edges, then feeds the result into `Graph.graphData()`. Schedules two `LevelCircles.updateLevelCircles` calls at 500 ms and 1500 ms to catch the physics settling.

---

## Data flow

```
User action
    │
    ├─ Keyboard/button ──► UI fires CustomEvent ──► app.js listener
    │                                                    │
    │                                              mutates State
    │                                              calls updateGraph()
    │                                                    │
    ├─ Node click/drag ──► app.js handler ─────────► Graph.nodeThreeObject()
    │                                                    │
    │                                         Rendering.createNodeObject()
    │                                         (reads flags from State at call time)
    │
    └─ RAF loop (Animation.startLoop)
           │
           ├─ Rendering.updateTime()          ← hole shader
           ├─ Animation.updateEdge()          ← edge flash
           └─ LevelCircles.updateLevelCircles() ← hierarchy circles
```

---

## Key design decisions

**No bundler.** All modules are plain `<script>` tags loaded in dependency order. No imports, no build step. Everything is a global object.

**State is a global mutable singleton.** Modules read `State` freely. `GraphUtils` and `Rendering` were refactored to be pure (taking data as arguments) so they can be reasoned about without State setup.

**CustomEvent seam between UI and app.** UI fires named browser events (`babel:create`, `babel:delete`, etc.) rather than calling passed-in callbacks. This lets UI be changed without touching app.js's event wiring, and vice versa.

**GraphUtils is pure.** `pruneTransitiveEdges(edges)` and `breakCycles(edges)` return lists of edges to remove; they never call `State.removeEdge` themselves. The caller (app.js or `cleanGraphOnLoad`) applies the results.

**Rendering is pure.** `createNodeObject(node, { isSelected, isDeleteWarning })` takes flags from its caller. It does not read `State.selectedBabel` or `State.deleteWarningBabel` directly.

**Preview lives in Rendering.** `Rendering.createPreview(container, color)` owns the Three.js scene, camera, renderer, and rotation loop for the edit-mode babel preview. UI holds only the returned handle (`{ updateColor, destroy }`).

**LevelCircles owns drag state.** All circle-drawing logic (static and drag-aware) and the drag tracking state live in one module. `Animation` only owns the edge flash and the RAF loop coordination.
