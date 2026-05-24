---
name: gap-analysis
description: >
  Requirements gap and contradiction analysis skill.
  Invoke to stress‑test captured requirements for missing pieces, conflicts, vagueness.
  Keywords: "gap analysis", "stress test", "review requirements", "find gaps", "challenge requirements", "contradiction check".
---

## Core Constraints (Cache)

CRITICAL RULES:
* NEVER propose fixes, solutions, or alternatives.
* NEVER judge requirements as "good" or "bad"—only flag clarity and consistency.
* NEVER ignore vague terms—always flag them.
* Output MUST follow the structured format below, grouping findings.

## Output Format (Cache)
```
## Gap Analysis
### Contradictions & Conflicts
- [Statement A] vs [Statement B] – resolution needed.
### Vague or Unmeasurable Claims
- "Make it faster" – define target, measurement, baseline.
### Missing Perspectives / Edge Cases
- No mention of ...
### Unvalidated Assumptions
- ...
```

## Analysis Logic

**1. Ingest Requirements Snapshot**
* Condition: Received a structured summary (captured from Phase 1) containing goals, scope, constraints, actors, risks, etc.
* Action: Process each statement individually.

**2. For Each Statement, Run Checks**
* Check measurability: Is it vague? Flag if it lacks a unit, target, or comparison.
* Check consistency: Does it contradict another statement? Explicitly pair conflicting statements.
* Check completeness: Are there missing actors, error states, non‑functional needs (security, performance)? List them.
* Check assumptions: What implicit beliefs is the statement built on? Surface them.

**3. Compile Challenge List**
* Group into the four categories above.
* Each entry must be a sharp, direct question phrased to force a resolution—not a suggestion.
  * "You said X, but earlier Y. How do they reconcile?"
  * "What happens if [worst‑case edge] occurs?"
  * "What concrete metric makes 'fast enough'?"

**4. Output**
* Present the gap analysis in the specified format.
* If no gaps are found (rare), output: "No inconsistencies, vagueness, or missing areas detected." but still note any assumptions that should be validated.

## Negative Constraints
* DO NOT add "recommended actions" sections.
* DO NOT soften language; challenges must be confrontational to be effective.
