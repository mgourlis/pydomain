# ADR-056: Declarative Resume Authorization via `resumes_from` and `should_resume`

## Status

Accepted

## Date

2026-05-22

## Context

Sagas suspend at specific steps to wait for external events ([ADR-034](ADR-034-saga-suspension-with-timeout.md)).
The `should_resume()` method was the only mechanism for filtering which events could wake a
suspended saga. For sagas with a single suspension point, a simple `isinstance` check sufficed:

```python
def should_resume(self, event: DomainEvent) -> bool:
    if self.state.status == SagaStatus.SUSPENDED:
        return isinstance(event, (FraudReviewApproved, FraudReviewRejected))
    return True
```

However, when a saga has **multiple distinct suspension points** (e.g., waiting for fraud
review at step `logging_fraud_flag`, then later waiting for inventory check at step
`awaiting_inventory`), the global override degenerates into an unwieldy `if/elif` chain:

```python
def should_resume(self, event: DomainEvent) -> bool:
    if self.state.current_step == "logging_fraud_flag":
        return isinstance(event, (FraudReviewApproved, FraudReviewRejected))
    elif self.state.current_step == "awaiting_inventory":
        return isinstance(event, (InventoryAvailable, InventoryUnavailable))
    elif self.state.current_step == "manager_signoff":
        return isinstance(event, ManagerApproved) and event.level == "VP"
    return False
```

The intent — "this event resumes from this step" — is buried in imperative logic. Adding a
new suspension point requires modifying the central `should_resume()` method, creating a
coupling point across unrelated saga steps.

## Decision

We will add two parameters to `Saga.on()`:

- **`resumes_from: str | list[str] | None = None`** — the step name(s) this event is authorized to resume. `None` means unrestricted (any step).
- **`should_resume: Callable[[Any], bool] | None = None`** — an inline predicate for step-specific resume logic. Receives the event, returns `True` to allow resume.

These are stored in two internal dictionaries on the saga instance:

```python
# Maps EventType -> Set of step names that this event can resume
self._resume_map: dict[type[DomainEvent], set[str]] = {}

# Maps EventType -> Inline predicate function
self._resume_predicates: dict[type[DomainEvent], Callable[[Any], bool]] = {}
```

The base `should_resume()` method is upgraded with a three-tier evaluation:

```python
def should_resume(self, event: DomainEvent) -> bool:
    event_type = type(event)

    # 1. Step-based authorization (resumes_from)
    if self._resume_map:
        allowed_steps = self._resume_map.get(event_type)
        if not allowed_steps or self.state.current_step not in allowed_steps:
            return False

    # 2. Inline predicate evaluation
    predicate = self._resume_predicates.get(event_type)
    if predicate is not None:
        return predicate(event)

    # 3. Fallback
    return True
```

**Backward compatibility:** If a subclass overrides `should_resume()`, the base logic is
completely bypassed — the override takes full control. This preserves the escape hatch for
unusual cases that cannot be expressed declaratively.

**Registration:** `resumes_from` normalizes a single string to a list. Duplicate
registrations for the same event type merge (set union). An empty list creates an entry
with an empty set, blocking the event at all steps.

**Example — multi-suspension saga with declarative resume gating:**

```python
class OrderFulfillmentSaga(Saga[OrderSagaState]):
    def __init__(self, state: OrderSagaState) -> None:
        super().__init__(state)

        # Suspension point 1: fraud review
        self.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlagCommand(...),
            step="logging_fraud_flag",
            suspend=True,
            suspend_reason="Awaiting manual fraud review.",
        )

        # Resume from fraud review — only FraudReviewApproved wakes this step
        self.on(
            FraudReviewApproved,
            send=lambda e: RequestShipmentCommand(order_id=e.order_id),
            step="requesting_shipment",
            resumes_from="logging_fraud_flag",
            should_resume=lambda e: e.agent_role == "SENIOR_MANAGER",
        )

        # Also resumes from fraud review, but with a different action
        self.on(
            FraudReviewRejected,
            send=lambda e: NotifyCustomerOfCancellationCommand(...),
            fail=True,
            fail_reason=lambda e: f"Agent {e.agent_id} rejected the order.",
            resumes_from="logging_fraud_flag",
        )

        # Suspension point 2: inventory check (different events, different step)
        self.on(
            InventoryCheckRequested,
            send=lambda e: ReserveInventoryCommand(...),
            step="awaiting_inventory",
            suspend=True,
            suspend_reason="Waiting for warehouse confirmation.",
        )

        self.on(
            InventoryAvailable,
            send=lambda e: RequestShipmentCommand(order_id=e.order_id),
            resumes_from="awaiting_inventory",
        )
```

No global `should_resume()` override is needed — each event declares which step it can
resume, and the base class enforces the isolation.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Global `should_resume()` only (status quo) | Degenerates into an `if/elif` chain for multi-suspension sagas. Couples unrelated suspension points. Adding a new step requires modifying a central method. |
| Decorator-based registration (e.g., `@resumes_from("step_name")` on handler methods) | Separates the resume authorization from the `on()` call where the step is defined. The `on()` DSL already groups all behavior for an event type; adding authorization there keeps everything in one place. |
| Event-to-step mapping table (e.g., a class-level dict) | Less readable than inline parameters. A table separates the authorization from the event handler definition, losing locality. |
| Per-step state machine (separate saga per suspension) | Over-segments the process. A single saga with multiple suspension points accurately models a single long-running business process. |

## Consequences

### Positive

- **Locality**: The resume authorization lives next to the event handler — reading one `on()` call tells you everything about that event's role in the saga.
- **Step isolation**: Each suspension point is independent. Adding a new suspension point with new events does not require touching existing code.
- **Predicate scoping**: Inline `should_resume` predicates are scoped to a single event type — no need to check `isinstance` inside the predicate.
- **Backward compatible**: Existing sagas with `should_resume()` overrides continue to work unchanged. The base logic is only active when no override exists.
- **Empty `_resume_map` gate**: If no `resumes_from` is registered on any `on()` call, `_resume_map` is empty and the base `should_resume()` skips directly to the predicate check and fallback — preserving the pre-ADR-056 behavior of unrestricted resume.

### Negative

- The `on()` method signature gains two more parameters (`resumes_from`, `should_resume`), compounding the density noted in ADR-028.
- The three-tier evaluation (map → predicate → fallback) is non-trivial and must be understood by developers debugging resume behavior.
- An empty `resumes_from=[]` creates a permanently-blocked event — this is intentional but may surprise developers who expect "empty list means no restriction."

### Neutral

- Empty list behavior (`resumes_from=[]` → event blocked at all steps) is documented and test-covered. It's consistent with the set-membership check: an event with an empty allowed-steps set can never match any step.

## References

- `src/pydomain/cqrs/saga/saga.py` — `_resume_map`, `_resume_predicates`, `should_resume()`, `on()` method
- [ADR-028](ADR-028-saga-on-dsl.md) — Saga `on()` DSL for unified command and compensation
- [ADR-034](ADR-034-saga-suspension-with-timeout.md) — Saga suspension with timeout (human-in-the-loop)
- `tests/saga/test_saga_resume_declarative.py` — Basic resumes_from/should_resume tests
- `tests/saga/test_saga_resume_edge_cases.py` — Edge-case and unhappy-path tests for resume authorization
- `tests/saga/test_saga_new_features_integration.py` — Multi-suspension and combined-feature integration tests
