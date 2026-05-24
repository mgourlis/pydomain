---
name: diagramming
description: >
  Generate, design, and review technical diagrams (Mermaid, Excalidraw). Trigger when the user
  asks to "draw", "diagram", "visualize", "map out", or create flowcharts, architecture diagrams,
  sequence diagrams, ERDs, mind maps, or system topologies.
---
# Technical Diagramming Skill

**Philosophy:** Understandable first, beautiful second. Every visual element must encode meaning (Tufte's Data-Ink Principle).

## 1. Mandatory References (Cache)
Do not guess syntax or layout math. Load the appropriate reference file into context before generating:
* `@references/design-principles.md` → Load for **Layout Math**, Gestalt principles, complexity budgets, and semantic hex colors.
* `@references/excalidraw.md` → Load for **JSON Schemas**, Z-index ordering, coordinate templates, and bidirectional arrow binding.
* `@references/mermaid.md` → Load for **Syntax Constraints**, agentic pitfalls, node shapes, and platform compatibility.

---

## 2. Format Decision Engine
**Use Mermaid when:**
* Lives in markdown (README, PRs, wikis).
* Auto-layout is acceptable; precise spatial positioning is not needed.
* Version control (text diffs) and rapid iteration are priorities.

**Use Excalidraw when:**
* Precise spatial layout is required (e.g., grouped architecture tiers).
* Freeform elements or non-standard visual topologies are needed.
* Visual polish is critical (presentations, formal reviews).
* The user needs to manually edit the diagram post-generation.

---

## 3. Spatial Reasoning Strategy (LLM Guidelines)

**Excalidraw Strategy (Coordinate Math):**
1. **Never invent coordinates randomly.** Select a layout template from the Excalidraw reference (Grid, Linear, Hub-and-Spoke, Tiered).
2. **Calculate before placing.** Count the elements, determine grid dimensions (multiples of 50px), and map out coordinates systematically.
3. **Anchor First.** Place the core element (Center or Top-Left) and position everything else relative to it using standard gaps (Horizontal: 250px, Vertical: 150px).
4. **Enforce Defaults:** `roughness: 0`, `fillStyle: "solid"`, `strokeWidth: 2`, `fontFamily: 5` (Excalifont), `roundness: { "type": 3 }`.

**Mermaid Strategy (Auto-Layout):**
1. **Do not fight the engine.** Use subgraphs to force logical grouping.
2. **Direction matters.** Use `LR` (Left-to-Right) for processes; `TD` (Top-to-Bottom) for hierarchies.
3. **Label lengths:** Keep labels under 5 words to prevent auto-layout breakdown. Use aliases for long participant names.

---

## 4. Diagram-Specific Heuristics
* **Flowcharts:** 3–10 steps (Max 15). 1 consistent flow direction. Diamond = Decision.
* **Architecture:** Frame/Subgraph by tier. Blue=Frontend, Yellow=Backend, Green=Data, Purple=External.
* **Sequence:** 3–6 participants, 10–15 messages. Time flows Top-to-Bottom. Number messages if order matters.
* **ER Diagrams:** Grid layout. Include PK/FK. Label lines with verb and cardinality.
* **Mind Maps:** Radial layout. 4–6 main branches max.

---

## 5. Execution & Quality Gate

**Output Rules:**
1. **Mermaid:** Embed inline using ```mermaid``` blocks.
2. **Excalidraw:** Save as `<descriptive-name>.excalidraw`. Tell the user they can view it via Excalidraw.com, VS Code, or Obsidian.
3. **Complexity Check:** If >20 elements, STOP. Offer to split into 1 High-Level Overview + localized Detail Diagrams.

**Pre-Delivery Checklist:**
* [ ] **Budget:** Element count ≤20.
* [ ] **Legibility:** Minimum font size 14px. No text overlapping shapes.
* [ ] **Semantics:** Color encodes meaning, not decoration. Legend included if 3+ colors used.
* [ ] **Excalidraw specific:** All IDs unique. Arrows use bidirectional `boundElements` and `endBinding`. Z-Index array order is correct (Zones → Frames → Shapes → Arrows → Text).
* [ ] **Mermaid specific:** No unquoted special characters. No lowercase `end` IDs.
