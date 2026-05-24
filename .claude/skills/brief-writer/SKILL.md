---
name: brief-writer
description: >
  Multi-phase DRAFT brief formatter and file writer.
  Invoke whenever a DRAFT phase completes and the user wants to save a structured brief,
  or when explicitly requested to write a phase artifact.
  Keywords: "write brief", "save brief", "create brief", "discovery brief",
  "research brief", "alignment brief", "frame brief", "tasks brief",
  "draft output", "phase artifact".
arguments: [phase, slug]
argument-hint: "<phase> <short-description-slug>"
---

## Core Constraints (Cache)

* NEVER add content not explicitly confirmed by the user in the current conversation.
* NEVER invent goals, risks, findings, or decisions—only synthesise from provided input.
* If any required section lacks information, output "Not specified". DO NOT guess.
* If the input contains unresolved contradictions or gaps, REFUSE to write. Return:
  "Cannot produce brief: unresolved points remain: [list]."
* Output MUST follow the exact template for $phase, loaded from its reference file.
* File path: `docs/design-docs/$slug/$slug.$phase.brief.md`. Create directory if missing.
* Phase-to-template mapping (load the file when needed):
  * discovery -> `@references/discovery-brief.md`
  * research -> `@references/research-brief.md`
  * frame -> `@references/frame-brief.md`
  * tasks -> `@references/tasks-brief.md`

## Execution Pipeline

**1. Validate Input**
* Condition: $phase missing or $slug missing.
* Action: Return "Usage: `/brief-writer <phase> <slug>`. Valid phases: discovery, research, alignment, frame, tasks."
* If $phase is invalid, return "Unknown phase '$phase'. Valid phases: discovery, research, alignment, frame, tasks."

**2. Load Template**
* Condition: Input valid.
* Action: Read the template file for $phase from the mapping above (e.g., `references/discovery-brief.md`).
* If file cannot be read, return "Template file for '$phase' not found at expected path."

**3. Extract Content**
* Action: Review the conversation context for verified, user‑confirmed statements relevant to $phase.
* If no suitable content is found, return "No verified content found for a $phase brief. Run the $phase phase first."

**4. Validate Completeness**
* Action: Scan extracted content for contradictions, gaps, or flagged unresolved items.
* If any remain, REFUSE to write and list them.

**5. Format Brief**
* Action: Using the loaded template, fill each section with only extracted content.
* Use the user's own terminology; keep bullets to one sentence.
* For any section with no content: write "Not specified".

**6. Write File**
* Action: Build path `docs/design-docs/$slug/$slug.$phase.brief.md`.
* Create directory `docs/design-docs/$slug/` if it doesn't exist.
* Write the formatted brief.
* Return: "Brief written to `docs/design-docs/$slug/$slug.$phase.brief.md`."

## Negative Constraints
* DO NOT add commentary, "next steps", or extra sections beyond the brief.
* DO NOT combine multiple phases into one brief.
* DO NOT write files outside `docs/design-docs/$slug/`.
* DO NOT modify the template structure for any phase.
