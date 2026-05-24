---
name: llm-wiki
description: >
  Build, query, and maintain an LLM-optimized wiki from Diataxis and ADR docs.
  Trigger on: "llm wiki", "wiki ingest", "compile docs to wiki", "update wiki",
  "wiki lint", "wiki health check", "wiki graph", "wiki map", "knowledge base",
  "build wiki", "generate wiki", "wiki query", "search wiki", "wiki synthesise",
  "wiki analyse", "wiki insights".
---
# LLM Wiki Manager

## Core Constraints & Structure (Cache)
* **Wiki Root:** `docs/llm-wiki/` (override via `llm-wiki-config.yaml`).
* **CRITICAL RULE:** Wiki is a read-only compiled view. NEVER modify original docs here.
* **Entry Points (Lookup Order):** 1. `catalog.md` (fast flat lookup/TL;DR), 2. `_index.md` (logical tree), 3. `log.md` (recent activity).
* **Page Formatting:** Name files `{lowercase-kebab-concept}.md`.
* **Required Sections:** `## TL;DR`, `## Details`, `## Rules`, `## Related`, `## Source`.
* **Links:** All cross-references MUST use `[[wikilinks]]`.

---

## Operations & Workflows

**1. Init (One-Time Setup)**
* **Condition:** Triggered explicitly or if config is missing. If config exists, ask before overwriting.
* **Run:** `python ${CLAUDE_SKILL_DIR}/scripts/init_config.py --project-dir .` (Scans layout, copies `${CLAUDE_SKILL_DIR}/references/config-template.yaml`).
* **Output:** Tell user: "Config created at `./llm-wiki-config.yaml`. Edit it or run `wiki ingest`."

**2. Ingest (Compile Docs → Wiki)**
* **Run:** `python ${CLAUDE_SKILL_DIR}/scripts/ingest.py --docs-dir docs/ --output-dir docs/llm-wiki [--config llm-wiki-config.yaml]`
* **Execution Details:** Extracts TL;DR/Rules/Summary. Groups by Diataxis modes. Extracts ADR number/title/intro. Updates `catalog.md`, `_index.md`, and appends timestamped counts to `log.md`.
* **Post-Ingest Run:** `python ${CLAUDE_SKILL_DIR}/scripts/lint.py --wiki-dir docs/llm-wiki --quick`
* **Output:** Report pages created/updated and warnings.

**3. Synthesise (Analyze Insights)**
* **Scan:** Load `docs/llm-wiki/_index.md` to discover pages. Read all (excluding `analysis/`), focusing on TL;DR, Rules, Related.
* **Generate/Update in `analysis/` (Max 500 words each):**
  * `ubiquitous-language.md`: Core domain terms and definitions.
  * `patterns.md`: Recurring design patterns.
  * `contradictions.md`: Conflicting advice or gaps.
  * `antipatterns.md`: Aggregated pitfalls.
  * `dependencies.md`: Recommended reading order.
  * `gaps.md`: Missing or underspecified areas.
* **Update Indexes:** Generate `analysis/_index.md` (ToC) and link it in the main `_index.md` under `## Analysis`.
* **Output:** Report counts per category.

**4. Query**
* **Lookup Path:** Check `log.md` (recent activity) → Scan `catalog.md` (fast title/keyword lookup) → Traverse `_index.md` via `[[wikilinks]]`.
* **Output:** Synthesize answer using `[Source](path)` citations.

**5. Lint**
* **Run:** `python ${CLAUDE_SKILL_DIR}/scripts/lint.py --wiki-dir docs/llm-wiki [--strict]`
* **Checks:** broken wikilinks, orphans, missing TL;DR, stale sources, catalog consistency. Appends summary to `log.md`.

**6. Graph**
* **Run:** `python ${CLAUDE_SKILL_DIR}/scripts/graph.py --wiki-dir docs/llm-wiki`
* **Output:** Emits ```mermaid mindmap``` block.
