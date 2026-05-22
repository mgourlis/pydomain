---
name: diataxis-writer
description: >
  Generate and maintain Diataxis-structured documentation for any project.
  Diataxis classifies docs into four modes: Tutorials (learning-oriented),
  How-to Guides (task-oriented), Reference (information-oriented), and
  Explanation (understanding-oriented). Use this skill whenever the user
  wants to create new documentation, set up a documentation structure,
  fill in missing docs, update docs after code changes, lint/audit
  documentation quality, convert links between formats, or organize
  existing documentation. Also triggers on mentions of "Diataxis",
  "documentation structure", "docs folder", "write docs for", "document
  this feature", "update the docs", "fix documentation links", "convert
  links to Obsidian/Confluence/YouTrack", or any documentation initiative
  that needs a systematic framework.
---

# Diataxis Documentation Writer

Apply the [Diataxis](https://diataxis.fr/) framework to create and maintain
high-quality, well-structured documentation. Diataxis classifies all
documentation into four modes based on what the user needs, preventing the
most common documentation failure: mixing modes within a single page.

## Mode Quick Reference

| Mode | Need | Orientation | Title Pattern |
|------|------|-------------|---------------|
| Tutorial | "Teach me" | Learning | "Getting started with X" or "X tutorial" |
| How-To | "Help me do X" | Task/goal | "How to [verb phrase]" |
| Reference | "Give me facts" | Information | "[Component] reference" |
| Explanation | "Help me understand" | Understanding | "Understanding X" or "About X" |

For detailed mode characteristics, anti-patterns, and the compass decision
tool, read `@references/diataxis-principles.md`. For per-mode writing
templates, read `@references/writing-conventions.md`. For link format
specifications, read `@references/link-types.md`.

## Six Capabilities

This skill provides six capabilities. Determine which one the user needs
based on their request, then follow the corresponding workflow.

### 1. Initialize Folder Structure

**When:** User wants to set up docs from scratch, create a documentation
structure, or organize existing docs into Diataxis format.

**Workflow:**

1. Determine the target directory (default: `docs/` in the project root).
   If the user has existing docs outside this directory, ask whether to
   move them or link to them.

2. Run the initialization script:
   ```bash
   python .claude/skills/diataxis-writer/scripts/init_structure.py \
     --target-dir docs --project-dir .
   ```
   This creates the four-quadrant directory layout and an `introduction.md`.

3. The script detects project metadata from `pyproject.toml`, `package.json`,
   and `README.md`. Review the generated `introduction.md` and refine it:
   - Is the project description accurate and concise?
   - Does the design philosophy section reflect the project's actual principles?
   - Are the module links correct?
   - Copy the quick links pattern for any additional modules the script missed.

4. If the project has a requirements/blueprint file (e.g.,
   `docs/others/diatiaxis-library-template.md`) that specifies planned pages,
   note its location — it will be used as the authoritative spec in the
   "Fill In" capability.

5. Report what was created and what the next step should be (usually
   "Fill In" for the most important modules).

### 2. Fill In Documentation

**When:** User wants to populate empty sections, complete missing docs, or
fully document a module/feature area.

**Workflow:**

1. **Discover what needs writing.** Check for a project blueprint/template
   file. If one exists (e.g., `docs/others/diatiaxis-library-template.md`),
   read it and extract the list of planned pages. Otherwise, analyze the
   project's source structure:
   - List source modules (e.g., `src/pydomain/ddd/`, `src/pydomain/cqrs/`)
   - List public classes/functions in each module
   - Cross-reference against existing docs to find gaps

2. **Prioritize.** Order pages by: adoption level (lower levels first),
   dependency chain (prerequisites before dependents), and user impact
   (most-used features first).

3. **For each missing page, use the compass to choose the mode:**
   - Is this action or cognition? Acquisition or application?
   - Read `@references/diataxis-principles.md` for the full decision framework
   - Read `@references/writing-conventions.md` for the template to use

4. **Write the page** following the template for its mode:
   - Use the project's actual API imports in code examples
   - Cross-reference ADRs and architecture docs where relevant
   - Link prerequisites at the top, next steps at the bottom

5. **Update the index.** Add the new page to its section's `_index.md`.

6. **Repeat** for each missing page. Batch independent pages together;
   write dependent pages in order.

### 3. Create Documents for New Features

**When:** User has added a new feature, module, class, or significant
logic and wants it documented.

**Workflow:**

1. **Understand the change.** Read the new or modified source files.
   Identify the public API surface: new classes, functions, methods,
   configuration options.

2. **Determine which Diátaxis modes are needed** using the compass:
   - Every new *concept* needs an **Explanation** page (what it is, why it exists)
   - Every new *action the user can take* needs a **How-To** page (how to use it)
   - If it changes the beginner experience, update the relevant **Tutorial**
   - Reference/API docs are handled by external tools (Sphinx, etc.)

   A new feature typically needs at minimum: one concept page + one how-to page.

3. **Check for ripple effects.** Does this change obsolete any existing
   documentation? Do tutorials need updated steps? Do related how-tos need
   new prerequisites or next-steps links?

4. **Write the pages** following `@references/writing-conventions.md` templates.

5. **Update cross-references:**
   - Add links from affected index pages
   - Update "Next steps" sections in related pages to point to the new docs
   - Add ADR references if a design decision was recorded

### 4. Update Existing Documents

**When:** User has modified the codebase and existing docs need to reflect
the changes.

**Workflow:**

1. **Identify affected pages.** Trace from the changed source files:
   - Which concept pages reference this module/class?
   - Which how-to pages use this API in their examples?
   - Which tutorials include steps involving this code?
   - Grep for the changed class/function names across `docs/`.

2. **Assess impact:**
   - API signature changes: update all code examples that use it
   - Behavioral changes: update concept pages that describe the behavior
   - Deprecated/removed features: add deprecation notices, update tutorials
   - New prerequisites: update how-to guide prerequisite lists

3. **Make changes surgically.** Update only what's affected. Preserve the
   mode discipline of each page — don't let explanation creep into how-tos
   during updates.

4. **Verify cross-references** still resolve correctly. Run the linter:
   ```bash
   python .claude/skills/diataxis-writer/scripts/lint_docs.py --docs-dir docs
   ```

### 5. Lint & Maintain Documentation

**When:** User wants to audit documentation quality, check for broken links,
find structural issues, or do periodic maintenance.

**Workflow:**

1. Run the linter:
   ```bash
   python .claude/skills/diataxis-writer/scripts/lint_docs.py --docs-dir docs
   ```
   Use `--strict` to treat warnings as errors (CI-friendly). Use
   `--skip-mode-check` to skip heuristic mode-discipline checks.

2. **Fix reported issues by priority:**
   - **Broken links** — fix immediately, these are user-facing errors
   - **Missing _index.md** — create index files for orphaned sections
   - **Empty sections** — either fill them in or add a planned-pages note
   - **Orphaned pages** — add them to the appropriate index, or remove if obsolete
   - **Mode discipline warnings** — review flagged pages, rewrite or split as needed
   - **Naming convention warnings** — rename files to follow conventions

3. **Manual review checklist** (things the script can't catch):
   - [ ] Every concept page has corresponding how-to pages for its operations
   - [ ] Every how-to page links to its prerequisite concepts
   - [ ] Tutorials still work end-to-end (test them)
   - [ ] Code examples still compile/run with the current API
   - [ ] ADR references are still accurate
   - [ ] No dead-end pages (pages with no "Next steps" links)
   - [ ] arch42 cross-references point to existing sections

### 6. Convert Links Between Formats

**When:** User wants to change the link format across the documentation,
migrate between platforms (e.g., Obsidian → GitHub), or add links in a
specific format.

**Workflow:**

1. Determine the source and target formats. Supported conversions:
   - `github` → `obsidian`: Standard Markdown links to `[[wikilinks]]`
   - `obsidian` → `github`: `[[wikilinks]]` to standard Markdown links
   - `github` → `confluence`: Requires `--base-url` for the Confluence instance
   - `github` → `youtrack`: Requires `--youtrack-url` for the YouTrack instance

2. For Confluence and YouTrack conversions, additional input is needed:
   - Confluence: base URL + ideally a page ID mapping
   - YouTrack: instance URL (ADR links are auto-mapped by number)

3. Run the conversion:
   ```bash
   python .claude/skills/diataxis-writer/scripts/convert_links.py \
     --from github --to obsidian --docs-dir docs/
   ```

4. Always do a `--dry-run` first to review the changes before applying:
   ```bash
   python .claude/skills/diataxis-writer/scripts/convert_links.py \
     --from github --to obsidian --docs-dir docs/ --dry-run
   ```

5. After conversion, run the linter to verify no links were broken:
   ```bash
   python .claude/skills/diataxis-writer/scripts/lint_docs.py --docs-dir docs
   ```

## General Principles

### Mode Discipline

The most important rule in Diataxis: **one page, one mode.** If you find
yourself blending modes, split the page:
- A how-to that starts explaining concepts → move the explanation to a concept page, link to it
- A concept page that ends with step-by-step instructions → move the steps to a how-to page
- A tutorial that offers alternatives → remove them, stay on the single path

### Link Direction

Cross-references flow in specific directions:
- **Tutorials** link to: concept pages (for deeper understanding), next tutorials
- **How-tos** link to: prerequisite concepts, related how-tos, reference pages
- **Concepts** link to: ADRs, related concepts, how-tos that use the concept
- **Reference** links to: concept pages (for context), how-tos (for usage)

### Code Examples

- Use the project's real public API, never pseudo-code
- Show complete imports — assume the reader starts from an empty file
- Use the project's test framework (e.g., pytest, pytest-anyio)
- Target the project's language version (e.g., Python 3.12+)
- Never show deprecated or internal APIs

### Project-Specific Customization

When a project has special conventions (adoption levels, ADR references,
arch42 sections, glossary terms), apply them consistently. Read the
project's existing docs first to understand the conventions in use.

If the project has a documentation blueprint/template file, treat it as
the authoritative spec for what pages need to be written. The blueprint
overrides what source-code analysis alone would produce.
