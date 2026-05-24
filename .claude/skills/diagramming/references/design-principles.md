# Visual Design Principles for Diagrams

## 1. Core Layout Constraints & Budgets (Cache)

**Complexity Budgets (CRITICAL: Split if >20 elements):**
* **Flowcharts:** 3-10 steps (Max 15).
* **Architecture:** 5-12 components (Max 20).
* **ER Diagrams:** 3-8 entities (Max 12).
* **Sequence:** 3-6 participants, 10-15 messages.
* *Splitting Strategy:* If exceeding budgets, generate one High-Level Overview + localized Detail Diagrams.

**Grid & Spacing Math (Coordinate Formats):**
* Align to multiples of **50px**.
* Horizontal gap between nodes: **200-300px**.
* Vertical gap between rows: **100-150px**.
* Group gap: **2x the intra-group gap** (Minimum 300px).
* Whitespace Rule: **60% background**, 30% primary content, 10% highlights. Leave minimum 50px canvas margins.

**Flow Direction:**
* Top-to-Bottom: Hierarchies, decision trees, inheritance.
* Left-to-Right: Processes, timelines, sequences.
* *Constraint:* NEVER mix flow directions for the same relationship type.

## 2. Semantic Color Palette
Use 2-3 accent colors max. Use **Monochromatic** defaults for professional docs (`#e9ecef` fill / `#1e1e1e` stroke, highlighting important items with `#a5d8ff`).
* **Blue (Info/Input/User):** bg `#a5d8ff` / stroke `#1971c2`
* **Green (Success/Data):** bg `#b2f2bb` / stroke `#2f9e44`
* **Yellow (Warning/Decide):** bg `#ffec99` / stroke `#f08c00`
* **Red (Error/Critical):** bg `#ffc9c9` / stroke `#e03131`
* **Purple (External/Storage):** bg `#d0bfff` / stroke `#9c36b5`
* **Gray (Neutral/Notes):** bg `#e9ecef` / stroke `#868e96`

## 3. Typography & Arrows

**Font Sizing:** Title (28-36px), Header (24px), Box (20px), Description (16px), Note (14px). *Absolute Minimum: 14px.*
**Font Constraints:** Sentence case for labels. Max 5 words per label. Horizontal text only.
**Excalidraw Fonts:** `2` (Helvetica - **Default for Tech Docs**), `3` (Cascadia - Code), `5` (Excalifont - Acceptable), `1` (Virgil - Brainstorming only).

**Arrow Semantics & Routing:**
* **Routing:** Prefer Orthogonal (Elbow) arrows. **Minimize crossings at all costs** (rearrange nodes to prevent them).
* **Solid:** Primary flow, main sequence.
* **Dashed:** Response, return, optional path.
* **Dotted:** Reference, dependency, weak association.
* **Line (No Arrowhead):** Association, grouping.

## 4. Gestalt Principles (Spatial Reasoning)
* **Uniform Connectedness:** Elements enclosed in frames or connected by lines are perceived as groups.
* **Proximity:** Group related nodes tightly; separate unrelated groups with heavy whitespace.
* **Similarity:** Match shapes to concept types (e.g., all databases = cylinders). Color groups stronger than shape.
* **Good Continuation:** Avoid sharp direction changes. Let the eye follow smooth paths.
* **Figure-Ground:** Prevent crowding so nodes pop against the background.
* **Closure:** Use dashed boundaries for logical grouping without visual heaviness.

## 5. Anti-Patterns & Philosophy
* **Tufte's Data-Ink Principle:** Maximize meaning, minimize noise. Remove decorative borders. Every visual cue (color, shape, position) MUST encode information.
* **Rainbow Diagrams:** Using colors without semantic meaning.
* **Spaghetti Arrows:** Failing to rearrange nodes to minimize line crossings.
* **Text Walls:** Embedding paragraphs instead of abstracting into concise labels.
* **Missing Legend:** Using color/shape codes without explaining them in a corner frame.
