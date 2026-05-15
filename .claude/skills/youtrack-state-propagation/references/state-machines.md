# State Machines — YouTrack State Propagation

Understanding and working with YouTrack state machines for transition validation.

## State Machine Fundamentals

YouTrack uses state bundles to define issue lifecycles. A state bundle is an ordered list of values where:
- Values have an implicit order (the order they appear in the bundle)
- Some values are marked as resolved (`isResolved: true`)
- Transitions typically follow the order (Open → In Progress → Fixed → Verified)
- Workflows can restrict which transitions are allowed

## Default State Machines by Issue Type

### Bug State Machine

```
Open → In Progress → Fixed → Verified → Closed
  ↓         ↓           ↓         ↓
Won't Fix  Duplicate  Incomplete  Can't Reproduce
```

- **Open:** Newly reported, not yet investigated
- **In Progress:** Developer is working on a fix
- **Fixed:** Fix is implemented, awaiting verification
- **Verified:** QA confirmed the fix works
- **Closed:** Released/deployed, no further action
- **Won't Fix:** Intentional decision not to fix (from any state)
- **Duplicate:** Same as another issue (from Open)
- **Incomplete:** Not enough information to act (from Open)
- **Can't Reproduce:** Unable to replicate the bug (from In Progress)

### Feature/Story State Machine

```
Open → In Progress → Fixed → Verified → Closed
  ↓
Rejected
```

- Feature state machines are simpler (fewer terminal states)
- "Rejected" is typically the only non-standard terminal state
- Some projects skip "Verified" for features (Fixed → Closed)

### Task State Machine

```
Open → In Progress → Fixed → Verified
```

- Tasks often have the simplest state machine
- Some projects collapse Fixed+Verified into a single "Done" state
- Subtask states may be constrained by the parent issue's workflow

## Reading the State Machine from Discovery

From the discovery cache at `/memories/session/youtrack-$PROJECT-config.md`:

```markdown
## State Machines
### Default State Machine (applies to Bug, Task, Feature)
- Open → In Progress → Fixed → Verified → Closed
- Open → Won't Fix / Duplicate / Incomplete (terminal)
```

This means:
- The happy path is linear: Open → In Progress → Fixed → Verified → Closed
- From any non-terminal state, you can jump to a terminal state (Won't Fix, etc.)
- You cannot go from Open directly to Closed (skips the verification chain)
- Resolved states (isResolved: true) are: Fixed, Verified, Closed, Won't Fix, Duplicate, Incomplete

## Transition Validation

### Check if a Transition is Valid

Given current state `S_current` and target state `S_target`:

1. **If S_target is a terminal state:** Valid from any state (subject to workflow restrictions)
2. **If S_target is the next state in the happy path:** Valid
3. **If S_target is further down the happy path (skipping states):** May be permitted but warn the user
4. **If S_target is before S_current (moving backward):** Typically not allowed unless the project explicitly supports reopening

### Reopening Issues

Some projects allow transitioning from resolved states back to open:
```
Closed → Open (Reopened)
```

This is configured per-project. If the discovery cache doesn't indicate reopening support, assume it's not allowed and warn the user.

## State Detection from get_issue

When you fetch an issue via `mcp__youtrack__get_issue("PROJ-42")`, the state is available in the response. The exact field path depends on the YouTrack API version:

- `state` — standard field
- `customFields[].state` — if project uses a custom state field
- `isResolved` — boolean flag indicating if the issue is in a resolved state

## Workflow Constraints

Some projects add additional constraints beyond the state order:
- **Assignee required:** Cannot move to "In Progress" without an assignee
- **Estimation required:** Cannot move to "In Progress" without an estimate
- **Comment required:** Cannot resolve without a comment explaining the resolution
- **Review required:** Cannot close without a review

These constraints are not discoverable via the MCP tools — they're part of the project's workflow configuration. If a transition fails, report the error and suggest checking workflow requirements.

## Multi-State Fields

Some projects have multiple state fields (e.g., "State" and "Review State"). The discovery cache will list all state-type fields. When updating, ensure you're targeting the correct state field.

## State Transitions and Tags

Some workflows auto-manage tags on state transitions:
- Moving to "In Progress" might auto-remove `needs-triage`
- Moving to "Fixed" might auto-add `needs-review`
- Resolving might auto-remove `blocked-by-external`

This behavior is not discoverable via MCP. Observe tag changes after state transitions and adapt.

## Example: Validating a Transition

```
Issue: PROJ-42, current state: "Open"
User: "Move to In Progress"

1. Load state machine from cache: Open → In Progress → Fixed → Verified → Closed
2. "In Progress" is the next state after "Open" → valid
3. Apply: mcp__youtrack__update_issue("PROJ-42", fields: [{"name": "State", "value": "In Progress"}])
```

```
Issue: PROJ-42, current state: "Open"
User: "Close PROJ-42"

1. Load state machine: Open → In Progress → Fixed → Verified → Closed
2. "Closed" is not the next state — it's 4 steps away and is a terminal state
3. Warn: "Closing directly from Open skips In Progress, Fixed, and Verified. This is unusual. Did you mean 'Won't Fix' or should this go through the normal flow?"
4. If user confirms, apply the transition anyway (some projects allow direct-to-terminal)
```
