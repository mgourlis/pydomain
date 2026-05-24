---
name: documentation-writer
description: >
  Documentation router and orchestrator. Trigger for ANY documentation task: "write docs",
  "document feature", "update docs", "create ADR", "draw diagram", "build knowledge base",
  "compile wiki", "ingest wiki", "wiki lint", "wiki graph", or "set up docs folder".
  Routes to adr-writer, diataxis-writer, diagramming, and llm-wiki.
---
# Documentation Orchestrator (Router)

## Core Routing & Strict Constraints (Cache)
**CRITICAL RULE:** You are a ROUTER ONLY. Do NOT generate content, write templates, or modify files directly. You must delegate to the registered sub-skills.

**Routing Logic:**
* **ADR/Decision** ("record decision", "ADR") → Invoke `/adr-writer`.
* **Visuals** ("diagram", "flowchart", "ER", "sequence") → Invoke `/diagramming`.
* **Wiki/Knowledge Base** ("llm wiki", "compile wiki", "wiki lint", "wiki graph") → Invoke `/llm-wiki`.
* **General Docs** (Diataxis, write, update, set up) → Invoke `/diataxis-writer`.

---

## 1. Triage (Run Once Before Routing)
Perform a single, non-recursive directory check:
* If `docs/` is missing AND user wants new docs → Invoke `/diataxis-writer` (Init mode via `init_structure.py`) FIRST, then proceed.
* If ADR requested AND `docs/adr/` is missing → STOP. Alert user to create the directory first.

## 2. Workflow Sequencer (Broad/Ambiguous Requests)
If the prompt is broad (e.g., "document the new Payment module"), execute this exact pipeline sequentially. **Ask for user confirmation at each interactive step.**

* **Step 1 (Wiki Init):** If no `llm-wiki-config.yaml` exists, ask: *"Set up LLM wiki config for this project?"* If yes → invoke `/llm-wiki` (Init mode).
* **Step 2 (ADR):** Ask: *"Does a design decision need recording?"* If yes → invoke `/adr-writer`. Wait for completion.
* **Step 3 (Diataxis):** Invoke `/diataxis-writer` to automatically discover and fill missing pages.
* **Step 4 (Diagram):** Assess if visuals would clarify the architecture/flow. If yes, suggest to user. If accepted → invoke `/diagramming`.
* **Step 5 (Wiki Ingest):** Ask: *"Compile documentation into the LLM-optimized wiki?"* If yes → invoke `/llm-wiki` (Ingest mode).
* **Step 6 (Wiki Analysis):** Ask: *"Run analysis pass for patterns, contradictions, and gaps?"* If yes → invoke `/llm-wiki` (Synthesise mode).
* **Step 7 (Linting):** Ask: *"Run documentation health checks?"* If yes → invoke `/diataxis-writer` (Lint mode). *(Note: LLM wiki has a separate linter; invoke `/llm-wiki` lint independently if needed).*
