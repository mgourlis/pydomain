# Diataxis Principles Reference

## Core Heuristics & Mode Discipline (Cache)

**Strict Rule:** Every page must serve exactly ONE mode. Never mix modes.

**The Compass (Choosing the Mode):**
* Action (doing) + Acquisition (learning) = **Tutorial**
* Action (doing) + Application (applying) = **How-To Guide**
* Cognition (thinking) + Acquisition (learning) = **Explanation**
* Cognition (thinking) + Application (applying) = **Reference**

**Titling & Constraint Checklist:**
* **Tutorial:** "[Topic] tutorial" | "Getting started with [X]". (*No inline explanation; link to concepts.*)
* **How-To:** "How to [verb phrase]". (*No teaching language like "you will learn".*)
* **Explanation:** "[Topic]" | "About [topic]" | "Understanding [topic]". (*No instructional steps.*)
* **Reference:** "[Component] reference" | "[API] specification". (*No instructional language. Factual only.*)

---

## The Four Modes in Detail

### 1. Tutorial (Learning-Oriented)
A tutorial is a **lesson** — a guided experience to acquire new skills.
* **Characteristics:** First-person plural ("we") affirms the tutor-learner relationship. Shows the destination upfront. Delivers visible results early. Minimizes explanation ruthlessly. Ignores options/alternatives to stay on the path.
* **Structure:** 1. Opening (what we'll accomplish) → 2. Sequential steps with concrete results → 3. Expected output at checkpoints → 4. Closing.
* **Anti-patterns:** "In this tutorial you will learn...", leading with abstraction, offering choices mid-lesson, inline explaining.

### 2. How-To Guide (Task-Oriented)
A how-to guide is a **recipe** — directions for a competent user to solve a real problem.
* **Characteristics:** User's perspective ("How do I...", not "What can Y do?"). Goal-oriented and focused on action. Addresses real-world complexity (adaptable, not rigid). Includes judgment, not just procedure.
* **Structure:** 1. Title → 2. Prerequisites → 3. Logical steps with code → 4. Expected outcome → 5. Next steps.
* **Anti-patterns:** Tool-focused guidance, teaching/explaining inline, ambiguous titles.

### 3. Reference (Information-Oriented)
Reference is **austere technical description** — facts consulted rather than read.
* **Characteristics:** Neutral, objective, factual. Mirrors the structure of the product. Examples are for concise illustration, not instruction. Authoritative and unambiguous. *(Note: Focus on structural reference like configs/errors; API signatures are usually auto-generated).*
* **Anti-patterns:** Mixing instruction/explanation, injecting opinion or marketing, creative/varied writing styles.

### 4. Explanation (Understanding-Oriented)
Explanation is **discursive treatment** — providing context, background, and the *why*.
* **Characteristics:** Makes connections. Provides context (history, constraints, design decisions). Admits opinion and considers alternatives. Best read away from the product (the only mode you might "read in the bath").
* **Structure:** 1. Title → 2. Opening (why it matters) → 3. Body (context, rationale, alternatives) → 4. Connections to concepts → 5. Links to ADRs/Reference.
* **Anti-patterns:** Scattering explanation across other sections, creeping into instruction, treating it as a luxury.
