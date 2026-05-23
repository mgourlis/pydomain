# Subscriptions

> **Adoption Level:** 5 — Advanced Event Sourcing
> **Module:** `pydomain.infrastructure.subscription`
> **Prerequisites:** [Event Sourcing](event-sourcing.md), [Projections](projections.md), [Event Store](event-store.md)

## What is a Subscription?

A **Subscription** binds a [projection](projections.md) to one or more event types for durable, catch-up-based event processing. The `SubscriptionRunner` polls the global event log, filters events by type, and dispatches matching batches to each subscription — tracking progress via a [CheckpointStore](event-sourced-repositories.md) so restarts resume where they left off.

## Why Subscriptions?

Event-sourced systems emit a continuous stream of events. Subscriptions provide a structured way to react to those events:

| Use case | Example |
|----------|---------|
| **Build read models** | Maintain an `OrderSummaryProjection` updated on every `OrderPlaced` / `OrderCancelled` |
| **Publish integration events** | Translate `OrderShipped` domain events into `OrderShippedIntegrationEvent` for an external broker |
| **Trigger side-effects** | Send an email when `PaymentFailed` occurs |
| **Cross-aggregate projections** | Build a `CustomerLifetimeValue` projection from events spanning multiple aggregate types |

## The `Subscription` Data Class

```python
from pydomain.infrastructure.subscription import Subscription

@dataclass
class Subscription:
    subscription_id: str                            # Unique identity
    projection: EventSourcedProjection               # Target projection
    event_types: tuple[type[DomainEvent], ...]       # Event types to filter on
```

A subscription answers three questions: *what* projection to update, *which* events to listen for, and *where* to resume from (via the checkpoint keyed by `subscription_id`).

## The `SubscriptionRunner` ABC

```python
from pydomain.infrastructure.subscription import SubscriptionRunner

class SubscriptionRunner(ABC):
    def __init__(
        self,
        event_store: EventStore,
        checkpoint_store: CheckpointStore,
        *,
        poll_interval_seconds: float = 1.0,
        failure_backoff_seconds: float = 0.1,
    ) -> None: ...

    def add_subscription(self, subscription: Subscription) -> None: ...

    @abstractmethod
    async def process_batch(
        self, events: Sequence[DomainEvent], subscription: Subscription
    ) -> None: ...

    async def run(self) -> None: ...       # Polling loop
    async def run_once(self) -> None: ...  # Single pass (for tests)
    def stop(self) -> None: ...            # Graceful shutdown
```

Subclass `SubscriptionRunner` and implement `process_batch` to define what happens with matching events. The runner handles the plumbing: checkpoint loading, global log reading, event filtering, and checkpoint persistence.

## Catch-Up Processing Loop

```
┌──────────────────────────────────────────────────────┐
│ run() polling loop                                    │
│                                                       │
│  1. Load all checkpoints from CheckpointStore         │
│  2. Read global log from the furthest-behind position │
│  3. For each subscription:                            │
│     a. Slice events from its checkpoint               │
│     b. Filter by event_types                          │
│     c. Call process_batch(matching, subscription)     │
│     d. On success: save new checkpoint                │
│     e. On failure: sleep backoff, retry next cycle    │
│  4. If no new events: sleep poll_interval_seconds     │
│  5. Repeat until stop() is called                     │
└──────────────────────────────────────────────────────┘
```

## At-Least-Once Semantics

The checkpoint is updated **only after** `process_batch` succeeds. If `process_batch` raises, the checkpoint stays at its previous value, and the same events will be redelivered on the next cycle. This guarantees at-least-once delivery — projections must be idempotent.

## Checkpoint Tracking

Each subscription's progress is stored as an integer — the global event log version it has processed up to. The `CheckpointStore` protocol abstracts the persistence:

```python
from pydomain.es.checkpoint_store import CheckpointStore

class CheckpointStore(Protocol):
    async def load(self, subscription_id: str) -> int: ...
    async def save(self, subscription_id: str, checkpoint: int) -> None: ...
```

`load` returns `0` for unknown subscriptions (meaning "start from the beginning"). The `FakeCheckpointStore` in `pydomain.testing` provides an in-memory implementation for tests.

## Relationship to Other Components

- **EventStore.read_all(from_version)**: the global event log that the runner polls
- **EventSourcedProjection**: the projection base class with `checkpoint` tracking and `_when_*` dispatch
- **CheckpointStore**: persists subscription progress
- **MessageBroker / MessageSubscriber**: external pub/sub — subscriptions are the *internal* catch-up mechanism; the message broker handles *external* integration

## Design decisions

> **📌 ADR-052**: Checkpoint store and snapshot store are separate protocols. Checkpoints track subscription progress, snapshots capture aggregate state. They serve different purposes and have different access patterns.

## Common pitfalls

> **⚠️** **Projections must be idempotent.** At-least-once delivery means `process_batch` may receive the same events more than once after a failure. Use upserts or check for duplicate event IDs.

> **⚠️** **Don't block the polling loop.** `process_batch` runs synchronously within the loop. For expensive work, queue events and process them asynchronously.

## Next steps

- [Track Checkpoints](../../how-to/event-sourcing/track-checkpoints.md) — checkpoint store usage
- [Catch-Up Subscriptions Recipe](../../how-to/recipes/subscriptions-catchup.md) — end-to-end subscription pipeline
- [Publish Integration Events Recipe](../../how-to/recipes/publish-integration-events.md) — bridging to external brokers
