# ADR-029: Generic `Saga[S: SagaState]` Parameterized by State Type

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Each saga manages its own state: which steps have been executed, what compensations are queued, and domain-specific data (e.g., order ID, reservation ID). The saga's handlers need type-safe access to this state.

Using a generic `Saga` base class with `Any` state or no type parameter would force handlers to cast `self.state` on every access, losing type safety and making errors discoverable only at runtime.

## Decision

`Saga` is parameterized by its state type using PEP 695 generics:

```python
class Saga[S: SagaState]:
    state_class: ClassVar[type[SagaState]] = SagaState
    listens_to: ClassVar[list[type[DomainEvent]]] = []

    def __init__(self, state: S) -> None:
        self.state: S = state
```

Each concrete saga defines its state type at class definition:

```python
class OrderSagaState(SagaState):
    order_id: UUID | None = None
    reservation_id: UUID | None = None

class OrderSaga(Saga[OrderSagaState]):
    state_class = OrderSagaState
    listens_to = [OrderCreated, ItemsReserved, PaymentReceived]

    async def handle_order_created(self, event: OrderCreated) -> None:
        self.state.order_id = event.order_id
        # Type-safe access to OrderSagaState fields
```

This enables:
- **Type-safe state access**: `self.state.order_id` is typed as `UUID | None`, not `Any`.
- **Custom state fields**: Each saga adds its own domain-specific fields to `SagaState`.
- **State class override**: `state_class` ClassVar allows custom state types while providing a default.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `Saga` with `state: SagaState` (no generic) | Handlers must cast `self.state` to access custom fields; no compile-time safety |
| `Saga` with `state: Any` | Completely untyped; no IDE support; runtime errors only |
| Separate state per handler (no shared state) | Handlers cannot share data; no step coordination |

## Consequences

### Positive

- Handlers get full type inference for saga-specific state fields.
- Custom state types are first-class — subclass `SagaState` and declare the generic parameter.
- Default `SagaState` works for simple sagas that don't need custom fields.

### Negative

- Requires Python 3.12+ for PEP 695 generics (already required by the library).

### Neutral

- `state_class` ClassVar defaults to `SagaState` — simple sagas don't need to override it.

## References

- `src/pydomain/cqrs/saga/saga.py` — `Saga[S: SagaState]` generic base class
- `src/pydomain/cqrs/saga/state.py` — `SagaState` base
