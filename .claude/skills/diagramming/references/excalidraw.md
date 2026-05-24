# Excalidraw JSON Reference

## 1. Core Architecture & Constraints (Cache)

**File Structure:** Must contain `"type": "excalidraw"`, `"version": 2`, `"source": "https://excalidraw.com"`, `"elements": []`, `"appState": { "viewBackgroundColor": "#ffffff", "gridSize": 20 }`, and crucially, an empty **`"files": {}`** object (omitting files breaks rendering).

**Z-Index Ordering (CRITICAL):**
Elements MUST be ordered in the `elements` array strictly as follows to prevent obscuring:
1. **Background Zones** (semi-transparent grouping rects)
2. **Frames** (`type: "frame"`, `roughness: 0`)
3. **Shapes** (`rectangle`, `ellipse`, `diamond`)
4. **Arrows/Lines**
5. **Text** (Labels & Annotations MUST be last to render on top)

**Bidirectional Binding Rules (CRITICAL):**
* **Text inside Shapes:** Native Excalidraw does *not* support a `label` property on shapes. You must create TWO elements:
  1. The Shape (must list Text ID in `boundElements`).
  2. The Text (must set `containerId` to Shape ID).
* **Arrows connecting Shapes:** You must create bidirectional links:
  1. Source Shape (must list Arrow ID in `boundElements`).
  2. Target Shape (must list Arrow ID in `boundElements`).
  3. The Arrow (must define `startBinding` and `endBinding` pointing to the shapes).

**Base Element Requirements:**
Every single element MUST include these properties. Do not omit them:
`id`, `type`, `x`, `y`, `width`, `height`, `angle` (0), `strokeColor`, `backgroundColor`, `fillStyle` ("solid"), `strokeWidth` (2), `strokeStyle` ("solid"), `roughness` (0 for tech docs), `opacity` (100), `groupIds` ([]), `frameId` (null), `index` (e.g., "a0", text gets higher like "aZ"), `isDeleted` (false), `seed` (incrementing int), `version` (1), `versionNonce` (seed * 100), `updated` (timestamp), `link` (null), `locked` (false), `boundElements` (null or array), `roundness` (see shapes).

---

## 2. Element Types & Schemas

### Shapes (`rectangle`, `ellipse`, `diamond`)
* **Roundness:** Rectangle = `{ "type": 3 }`, Diamond = `{ "type": 2 }`, Ellipse = `null` (NEVER `{type: 3}`).
* **Sizing:** Standard Box = 180x80. Diamond = 140x100. Minimum w/ text = 80x60.
```json
{
  "type": "rectangle",
  "id": "step-1",
  "boundElements": [
    { "type": "text", "id": "step-1-label" },
    { "type": "arrow", "id": "arrow-1-to-2" }
  ]
}

```

### Bound Text (Labels)

* **Required Properties:** `containerId` (matches shape), `originalText`, `textAlign: "center"`, `verticalAlign: "middle"`, `lineHeight: 1.25`, `autoResize: true`, `backgroundColor: "transparent"`, `roundness: null`.
* **Fonts:** `fontFamily: 5` (Excalifont). `fontSize`: Title (28), Header (24), Box Label (18-22), Body (16).

```json
{
  "type": "text",
  "id": "step-1-label",
  "text": "User Service",
  "originalText": "User Service",
  "containerId": "step-1"
}

```

### Arrows & Binding

* **Points:** `[[0,0], [dx, dy]]`.
* **Arrowheads:** `"arrow"`, `"triangle"`, `"bar"`, `"dot"`, `null`.
* **Elbow Arrows:** Require `elbowed: true`, `roughness: 0`, `roundness: null`.

```json
{
  "type": "arrow",
  "id": "arrow-1-to-2",
  "points": [[0, 0], [220, 0]],
  "startBinding": { "elementId": "shape-a", "focus": 0, "gap": 1, "fixedPoint": [1, 0.5] },
  "endBinding": { "elementId": "shape-b", "focus": 0, "gap": 1, "fixedPoint": [0, 0.5] },
  "startArrowhead": null,
  "endArrowhead": "arrow"
}

```

*(Note: `fixedPoint` mapping -> Right: `[1, 0.5]`, Left: `[0, 0.5]`, Top: `[0.5, 0]`, Bottom: `[0.5, 1]`)*

---

## 3. Styling & Colors

**60-30-10 Rule Palette (Background / Stroke):**

* **Standard / Input:** `#a5d8ff` / `#1971c2`
* **Success / Data:** `#b2f2bb` / `#2f9e44`
* **Warning / Decide:** `#ffec99` / `#f08c00`
* **Error / Danger:** `#ffc9c9` / `#e03131`
* **External / Infra:** `#d0bfff` / `#9c36b5`
* **Neutral / Notes:** `#e9ecef` / `#868e96`
* **Background Zones:** `opacity: 35`, `strokeStyle: "dashed"`, `backgroundColor: "#a5d8ff"`.

---

## 4. Layout Math & Topologies

**Linear Flow (Left-to-Right):** `x += 280` per element.

* E1: `(100, 200)` -> E2: `(380, 200)` -> E3: `(660, 200)`

**Grid Layout (ER/Relations):**

* `columns = ceil(sqrt(n))`
* `x = start_x + (i % columns) * 300`
* `y = start_y + floor(i / columns) * 200`

**Hub-and-Spoke (Mindmaps):**

* `angle = (2pi * i) / n`
* `x = center_x + radius * cos(angle)`
* `y = center_y + radius * sin(angle)`

**Binary Decision Tree:**

* Level 0: 1 node at `x=400`
* Level 1: 2 nodes at `x=200`, `x=600` (spread = 200)
* Level 2: 4 nodes at `x=100`, `x=300`, `x=500`, `x=700` (spread = 100)

---

## 5. Diagram Conventions

* **Max Elements:** Target < 20 per diagram.
* **Flowcharts:** Start/End (Ellipse), Process (Rectangle), Decision (Diamond). Left-to-Right or Top-to-Bottom.
* **Architecture:** Components (Rectangles). Databases (Green). External (Purple). Group using Frames.
* **Sequence:** Time flows Top-to-Bottom. Lifelines use lines. Participants spaced `250px` apart horizontally. Messages `80px` apart vertically.
* **ER Diagrams:** Grid layout. Include cardinality (`1..*`) via text labels on lines.
