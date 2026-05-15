# Propagation Rules — YouTrack State Propagation

Complete rule set for propagating states across link types. Conflict resolution, confirmation requirements, and rollback strategy.

## Rule Categories

Propagation rules are organized by link type. Each rule specifies:
- **Trigger:** What state change triggers this rule
- **Scope:** Which linked issues are affected
- **Action:** What to do (suggest / warn / auto-apply with confirmation)
- **Conflict:** How to resolve if multiple rules apply

## Parent-Subtask Rules

### R1: Parent Resolve → Check Subtasks

**Trigger:** User resolves a parent issue (epic, story, or any with subtasks).

**Action:**
1. Fetch all subtasks
2. Check each subtask's state
3. If all subtasks are resolved: "All subtasks are resolved. Parent can be resolved."
4. If any subtask is unresolved: warn that they'll be left unresolved. Offer to resolve them: "PROJ-42 has 2 unresolved subtasks. Resolve them too?"

**Default behavior:** Warn, do not auto-resolve subtasks.

**Rationale:** Subtasks may have different owners or be intentionally deferred.

### R2: All Subtasks Resolved → Suggest Parent

**Trigger:** User resolves the last remaining unresolved subtask of a parent.

**Action:**
1. Fetch the parent issue
2. Check if ALL siblings are now resolved
3. If yes: "All subtasks of PROJ-10 are now resolved. Resolve PROJ-10 as well?"
4. If no: "3 of 5 subtasks of PROJ-10 are now resolved."

**Default behavior:** Suggest, do not auto-resolve parent.

### R3: Parent Reopens → Suggest Subtask Review

**Trigger:** User moves a parent issue from a resolved state back to an open state.

**Action:**
1. Fetch all subtasks
2. Show their current states
3. "PROJ-10 was reopened. Review its subtasks: 2 are resolved, 1 is open. Should any be reopened?"

**Default behavior:** Inform, take no automatic action.

## Dependency Rules

### R4: Dependency Resolved → Notify Dependents

**Trigger:** User resolves an issue that has "is required for" links (i.e., other issues depend on it).

**Action:**
1. Find all issues that depend on this one (via "is required for" links)
2. Check if they were blocked only by this dependency (they may have other unresolved deps)
3. Report: "Resolving PROJ-88 unblocks: PROJ-42 (was blocked only by this), PROJ-55 (has other unresolved dependencies)."
4. For fully unblocked issues, offer: "PROJ-42 is now ready to proceed. Move to In Progress?"

**Default behavior:** Report unblocked issues. Offer state change for fully unblocked ones.

### R5: Dependency Unresolved → Blocked Warning

**Trigger:** User tries to resolve an issue that has unresolved "depends on" links.

**Action:**
1. Fetch all dependencies
2. List unresolved dependencies
3. "PROJ-42 depends on PROJ-88 (Open). You can resolve PROJ-42, but the dependency is still open. Proceed anyway?"
4. If user confirms, resolve anyway (override warning).

**Default behavior:** Warn, let user decide.

### R6: Batch Dependency Check

**Trigger:** User asks "what can I work on" or "what's ready to proceed."

**Action:**
```
mcp__youtrack__search_issues(query: "project: $PROJECT State: Open has: {depends on}")
→ For each, check dependency states
→ Classify as:
  - BLOCKED: has at least one unresolved dependency
  - READY: all dependencies are resolved
  - UNCERTAIN: couldn't resolve dependency states
```

**Default behavior:** Report only, no state changes.

## Duplicate Rules

### R7: Original Resolved → Resolve Duplicates

**Trigger:** User resolves an issue that has duplicates (via "is duplicated by" links).

**Action:**
1. Find all duplicate issues
2. Check their current states
3. "PROJ-42 is being resolved. Its duplicates (PROJ-88, PROJ-99) are still open. Resolve them as duplicates?"
4. If confirmed, resolve each with a comment: "Resolved as duplicate of PROJ-42."

**Default behavior:** Suggest, confirm before acting.

### R8: Duplicate Marked → Close

**Trigger:** User marks an issue as a duplicate of another.

**Action:**
1. After linking: "PROJ-42 is now marked as a duplicate of PROJ-88. Resolve/close PROJ-42?"
2. If confirmed, add a comment and resolve.

**Default behavior:** Offer to resolve the duplicate.

## Relates-To Rules

### R9: Related Issue State Change → Inform Only

**Trigger:** User changes the state of an issue that has "relates to" links.

**Action:**
1. "PROJ-42 relates to PROJ-55. PROJ-55 is still Open. No dependency is implied, but FYI."
2. Take no further action.

**Default behavior:** Informational only. Never propagate on "relates to."

## Conflict Resolution

When multiple propagation rules fire simultaneously, apply this priority:

1. **Dependency rules** (R4, R5, R6) — blocking relationships take precedence
2. **Parent-subtask rules** (R1, R2, R3) — hierarchy is second priority
3. **Duplicate rules** (R7, R8) — cleanup comes after structure
4. **Relates-to rules** (R9) — informational always last

If two rules conflict (e.g., R1 says resolve subtasks, but R5 says a subtask is blocked by a dependency):
- Present the conflict to the user
- Suggest: resolve the dependency first, then the subtask, then the parent
- Never resolve through a conflict without user approval

## Confirmation Requirements

### Always Confirm

These operations always require explicit user confirmation:
- Resolving issues the user didn't explicitly name
- Resolving a parent when subtasks exist
- Closing duplicates
- Any batch operation affecting more than 1 issue
- Overriding a dependency warning (R5)

### Can Proceed Without Confirmation

These operations can proceed after simply informing the user:
- Reporting which issues are blocked/unblocked (read-only)
- Informing about related issues during a state change
- Single-issue state transitions requested by the user

## Rollback Strategy

### If Propagation Partially Fails

When a batch state operation has mixed results:
1. Report which issues succeeded and which failed
2. Do NOT attempt to roll back the succeeded ones — YouTrack issues don't have transactional rollback
3. Suggest manual reversion: "PROJ-100 and PROJ-101 were resolved but PROJ-102 failed. You may want to reopen PROJ-100 and PROJ-101."
4. Always ask before reverting

### Error Recovery

If a state transition fails:
- Report the specific error
- Check if it's a workflow constraint (missing assignee, comment required, etc.)
- Suggest the fix
- Retry after the user provides what's needed

## Example: End-to-End Propagation

```
User: "Resolve PROJ-10" (Epic)

1. Fetch PROJ-10 → type: Epic, state: In Progress, subtasks: [PROJ-42, PROJ-43]
2. [R1 triggers] Check subtasks:
   - PROJ-42: Story, In Progress, subtasks: [PROJ-100 (Open), PROJ-101 (Fixed)]
   - PROJ-43: Story, Open
3. Build plan:
   - PROJ-100: Open → Resolved (OK)
   - PROJ-101: already Fixed (skip)
   - PROJ-42: In Progress → Resolved (OK, after subtasks)
   - PROJ-43: Open → Resolved (OK)
   - PROJ-10: In Progress → Resolved (OK, after all subtasks)
4. Present plan: "Resolving PROJ-10 requires resolving 3 subtasks and 2 stories first.
   | Issue | Current | Target |
   |-------|---------|--------|
   | PROJ-100 | Open | Resolved |
   | PROJ-42 | In Progress | Resolved |
   | PROJ-43 | Open | Resolved |
   | PROJ-10 | In Progress | Resolved |
   Proceed?"
5. If confirmed, execute bottom-up:
   - Update PROJ-100 → Resolved
   - Update PROJ-42 → Resolved
   - Update PROJ-43 → Resolved
   - Update PROJ-10 → Resolved
6. Report: "Resolved 4 issues: PROJ-100, PROJ-42, PROJ-43, PROJ-10."
```
