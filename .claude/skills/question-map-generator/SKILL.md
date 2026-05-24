---
name: question-map-generator
description: >
  Question map generation engine for discovery.
  Trigger when discovery Phase 1 begins; input: user intent, output: tailored question map.
  Keywords: "question map", "discovery questions", "generate questions", "questionnaire", "what to ask".
---

## Core Constraints (Cache)

CRITICAL RULES:
* NEVER embed solutions, design ideas, or feature suggestions in questions.
* NEVER ask yes/no questions unless absolutely essential for clarity—prefer "how", "what", "why".
* Output MUST follow the exact structure below; do not deviate.
* If user intent is too vague to infer dimensions, note missing info as questions in a "Clarifications Needed" section.

## Output Template (Cache)
```
## Discovery Question Map
### Motivation
- ...
### Goals
- ...
### Scope
- ...
### Constraints
- ...
### Actors
- ...
### Risks
- ...
### Prior Art
- ...
## Clarifications Needed (if any)
- ...
```

## Generation Logic

**1. Parse Intent**
* Condition: User intent provided (one sentence to a few paragraphs).
* Action: Infer domain, possible actors, and constraints from language.
* DO NOT assume facts you don't have; treat unclear elements as needed clarifications.

**2. Build Dimension Questions**
* For each dimension (Motivation, Goals, Scope, Constraints, Actors, Risks, Prior Art):
  * Draft 2‑5 specific questions tied directly to the intent text.
  * Avoid generic phrasing; use the user's own terms.
  * Phrase to uncover measurable outcomes, explicit boundaries, hidden constraints.
  * Example for "a dashboard for inventory": not "What should the dashboard show?" but "What metrics must the inventory dashboard surface to reduce stock‑outs by 20%?"

**3. Output & Handoff**
* Output the question map exactly as per template.
* Append a note: "Review this map with the user. Ask them to prioritise, skip, or add dimensions before starting the conversation."

## Efficiency Directives
* Keep questions concise; one line each.
* Use bullet dash lists, no tables or markdown embellishments.
