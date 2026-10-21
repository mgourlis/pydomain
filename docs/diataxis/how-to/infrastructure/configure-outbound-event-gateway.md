# How to configure the outbound event gateway

<!-- docs-test: runnable -->

<!-- docs-test: setup
from collections.abc import Callable
from uuid import uuid4

from pydomain.cqrs.commands import Command, CommandResult
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import FakeOutboxStore, FakeRepository, InMemoryMessageBroker


class AsyncSession:
    """Stands in for a SQLAlchemy session."""

    async def flush(self) -> None: ...

    async def commit(self) -> None: ...


def session_factory() -> AsyncSession:
    return AsyncSession()


def OrderRepository(session: AsyncSession) -> FakeRepository:
    return FakeRepository()


class PlaceOrderResult(CommandResult):
    pass


class PlaceOrder(Command[PlaceOrderResult]):
    pass


class PlaceOrderHandler:
    async def __call__(self, command: PlaceOrder, uow: object) -> PlaceOrderResult:
        return PlaceOrderResult()


outbox_store = FakeOutboxStore()
rabbitmq_broker = InMemoryMessageBroker()
bus = MessageBus()
-->

> **Adoption Level:** 5 — Durable Outbound Delivery
> **Prerequisites:** [Integration Events](../../concepts/cqrs/integration-events.md), [Message Broker](../../concepts/infrastructure/message-broker.md), [Unit of Work](../../concepts/cqrs/unit-of-work.md)

This guide shows you how to deliver integration events without losing them to a broker outage, by persisting them in a transactional outbox and draining that outbox with a relay.

## 1. Implement the `OutboxStore` port

The outbox needs a table. Implement the five-method port against your database:

```python
from collections.abc import Sequence
from datetime import datetime

from pydomain.cqrs.outbox import OutboxEntry, OutboxStore


class SqlAlchemyOutboxStore(OutboxStore):
    """Outbox backed by a database table."""

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, entries: Sequence[OutboxEntry]) -> None:
        # Write through the caller's session so entries commit with the
        # aggregate state change.
        ...

    async def fetch_unpublished(self, limit: int = 100) -> list[OutboxEntry]:
        # Raise ValueError when limit < 1. Claim what you return: a later
        # fetch must not return a claimed row until it is marked or its
        # claim goes stale (a crashed relay). Exclude rows whose next
        # attempt time has not passed. On PostgreSQL, select with
        # FOR UPDATE SKIP LOCKED and set a claimed_until timestamp.
        ...

    async def mark_published(self, message_ids: Sequence[str]) -> None:
        # The relay calls this once per entry, right after its publish. A
        # dead-lettered row moves to published: the broker did receive it.
        ...

    async def mark_failed(
        self, message_id: str, *, error: str, next_attempt_at: datetime
    ) -> None:
        # Increment the row's attempts counter, or the relay's backoff
        # cannot grow and it can never tell when the budget is spent.
        # Release the claim: the row waits only for next_attempt_at.
        # A no-op for a row already marked published (a late failure from a
        # relay whose claim went stale).
        ...

    async def mark_dead_lettered(self, message_id: str, *, error: str) -> None:
        # Terminal. The row must never be returned by fetch_unpublished
        # again, and must never be reported as published. Whether it is a
        # status column or a separate table is your choice — but keep it
        # visible, because this is what an operator triages. Count this
        # final failure too, so the attempts counter does not under-report.
        # A no-op for a row already marked published.
        ...
```

Atomicity and concurrency are the adapter's responsibility; the port has no claim methods. Run [`OutboxStoreContract`](../testing/run-contract-suites.md) against your adapter: it checks the claim, the `limit` rule and the rest of the Protocol's promises ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)).

## 2. Register your outbound translations

Registration decides what leaves the bounded context. An unregistered domain event stays internal, silently.

```python
from typing import cast
from uuid import UUID

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry
from pydomain.ddd.domain_event import DomainEvent


class OrderPlaced(DomainEvent):
    order_id: UUID
    total: int


class OrderPlacedIntegration(IntegrationEvent):
    order_id: str
    total: int


def translate_order_placed(event: DomainEvent) -> OrderPlacedIntegration | None:
    """Pure: runs inside the database transaction, so no I/O."""
    placed = cast(OrderPlaced, event)
    return OrderPlacedIntegration(order_id=str(placed.order_id), total=placed.total)


registry = OutboundEventRegistry()
registry.register_translation(
    OrderPlaced,
    OrderPlacedIntegration,
    "orders.placed",
    translate_order_placed,
)
```

Return `None` from a translator to keep a specific event internal without unregistering its type. The translator must return an instance of exactly the registered class; anything else, a subclass included, raises `TypeError` and aborts the commit.

Re-registering a domain type **overwrites** the previous registration, which is how you hot-swap an integration event version. Rows already stored on the topic are then rehydrated as the new class, so it must accept them. A topic resolves to one class: several domain types may share a topic only when they translate to the same class, and registering a different class on a topic another domain type uses raises `ValueError`. A topic stays bound after its domain type moves to another topic, because rows already stored there still resolve to the old class.

## 3. Pass `OutboxWriter` to your Unit of Work

`AbstractUnitOfWork` implements `_write_outbox()` for you — hand it a writer in the constructor.

```python
from pydomain.cqrs.outbox import OutboxWriter
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork


class AppUnitOfWork(AbstractUnitOfWork):
    def __init__(self, session: AsyncSession, writer: OutboxWriter) -> None:
        super().__init__(outbox_writer=writer)
        self._session = session
        self.orders = OrderRepository(session)
        self._repos = {"orders": self.orders}

    async def _flush(self) -> None:
        await self._session.flush()

    async def _commit(self) -> None:
        await self._session.commit()
```

Build the writer once and close over it in the UoW factory:

```python
writer = OutboxWriter(store=outbox_store, registry=registry)

bus.register_command(
    PlaceOrder,
    PlaceOrderHandler(),
    uow_factory=lambda: AppUnitOfWork(session_factory(), writer),
)
```

`_write_outbox()` runs after domain events are collected and stamped, and before the transaction commits — so the entries land in the same transaction as the state change.

Omit the writer for the old no-op behaviour, or override `_write_outbox()` to customise. An override can still delegate with `await super()._write_outbox()`.

## 4. Build the gateway

```python
from datetime import timedelta

from pydomain.infrastructure.message_broker import OutboundEventGateway

gateway = OutboundEventGateway(
    store=outbox_store,
    broker=rabbitmq_broker,
    registry=registry,
    batch_size=100,
    base_delay=timedelta(seconds=5),
    max_delay=timedelta(minutes=30),
    max_attempts=10,
)
```

The constructor raises `ValueError`, naming the argument, for a `batch_size` or `max_attempts` that is not an `int` of at least `1`, a negative delay, a sleep that is negative or not finite, and a `max_delay` shorter than `base_delay`.

`max_attempts` is the retry budget: an entry that fails that many deliveries is dead-lettered instead of rescheduled, and the event is logged at `ERROR` level. The default is `10`. Set it to `None` to retry forever — appropriate only if you monitor the outbox another way, since a permanently broken row will then be refetched on every pass indefinitely.

The budget is uniform across failure kinds on purpose. The relay cannot tell a recoverable failure (an unknown topic that a later deploy will register) from a terminal one (a payload that can never validate), so it gives every failure the same number of attempts rather than guessing.

Alert on the dead-letter `ERROR` log, and make sure you can replay: clear the row's dead-letter state after fixing the cause and the relay picks it up again.

The `error` the relay passes to `mark_failed` and `mark_dead_lettered`, and its log lines, never contain payload values ([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)). They name the failure, the failing fields and Pydantic's error codes:

| Failure | Stored `error` | Log line (`ERROR`, no traceback) |
|---------|----------------|----------------------------------|
| Unknown topic | `No translation registered for topic 'orders.placed'` | `No translation registered for topic 'orders.placed' — <message id> requeued` |
| Invalid payload | `Payload validation failed: ValidationError: 1 error for OrderPlacedIntegration at total (int_parsing)` | `Payload validation failed for <message id> on topic 'orders.placed' — requeued: ValidationError: …` |
| Broker error | `Broker publish failed: ConnectionError` (the type only) | `Publish failed for <message id> on topic 'orders.placed' — requeued: ConnectionError` |
| Dead letter | The last failure's `error` | `Outbox entry <message id> on topic 'orders.placed' exhausted 10 attempts — dead-lettered: <error>` |
| A pass that raises (the store failed) | — | `Outbox relay pass failed: <type>; backing off` |

To see why a payload failed, read the row's `payload` under your own access rules and validate it against the integration class. The relay does not log a broker's stack trace, so log inside your broker adapter if you need one, without the message body. Parse these texts only by their prefixes; the descriptions after the colon may gain detail. See [Handle Sensitive Payloads](handle-sensitive-payloads.md).

Each entry is marked published right after the broker accepts it. When the entry carries tracing IDs, the relay stamps them onto the event and passes them as `headers={"correlation_id": ..., "causation_id": ...}`, so your broker's `publish()` must accept `**kwargs` ([Configure a Message Broker](configure-message-broker.md)). A store error ends the pass; the relay logs it, backs off and tries again, and the batch's undelivered entries come back once their claim goes stale.

Call `run_once()` directly for a single controlled pass — useful in tests and cron-driven deployments:

```python
published = await gateway.run_once()
```

## 5. Wire it into bootstrap

Pass the gateway to `bootstrap()`. It starts **after** the broker and is drained **before** the broker is stopped, in `Application.shutdown()`.

```python
from pydomain.infrastructure.bootstrap import bootstrap

app = await bootstrap(
    message_bus=bus,
    message_broker=rabbitmq_broker,
    outbound_gateway=gateway,
)

# ... serve traffic ...

await app.shutdown()
```

## 6. Run the relay

In a long-running process, `start()` schedules the polling loop as a background task:

```python
await gateway.start()
# ... application runs ...
await gateway.stop()   # drains the in-flight pass before returning
```

`stop()` waits for the in-flight pass, so a broker `publish()` that never returns blocks it: give your broker adapter a publish timeout. This is a known gap ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)).

For a separate worker process, call `run()` directly — it polls until `stop()` is called. A `stop()` made before a task you created for `run()` has begun is lost, so prefer `start()` when another task may stop the relay early. Use either `start()` or your own `run()` task, never both: after `start()`, `stop()` does not return.

<!-- docs-test: skip -->
```python
await gateway.run()
```

## Expected outcome

A command that changes aggregate state and produces a registered domain event leaves exactly one outbox row, committed atomically with the state change. The relay publishes that row and marks it published. If the broker is unavailable, the row stays unpublished and is retried with increasing backoff — the command still succeeds. If it fails `max_attempts` times, it is dead-lettered: no longer fetched, never reported as published, and logged at `ERROR`.

<!-- docs-test: check
entry = registry.to_entry(
    OrderPlaced(order_id=uuid4(), total=1).stamp(
        correlation_id=uuid4(), causation_id=uuid4()
    )
)
-->

Verify by asserting on the store and the broker after a single pass. `FakeOutboxStore` claims what it fetches, like a real adapter, so assert on `store.entries`, `store.published` and `is_published()` rather than calling `fetch_unpublished()` yourself:

```python
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker

store = FakeOutboxStore()
broker = InMemoryMessageBroker()
await store.append([entry])

gateway = OutboundEventGateway(store, broker, registry)
await gateway.run_once()

assert len(broker.published) == 1
assert store.is_published(entry.message_id)
```

## Next steps

* [OutboundEventGateway concept](../../concepts/infrastructure/outbound-event-gateway.md) — why the design is this way, and its pitfalls
* [Configure a Message Broker](configure-message-broker.md) — the broker the gateway publishes through
* [Configure an InboundEventGateway](configure-inbound-event-gateway.md) — the inbound counterpart
* [ADR-065: Outbox Retry Policy and Dead-Letter Queue](../../../adr/ADR-065-outbox-retry-policy-and-dead-letter.md) — the retry-budget rationale

## Cross-references

* **ADR-063**: [Outbound Event Gateway and Transactional Outbox](../../../adr/ADR-063-outbound-event-gateway-and-outbox.md)
* **ADR-064**: [`OutboxWriter` — Library-Owned Write-Side Join by Composition](../../../adr/ADR-064-outbox-writer-composition.md)
* **ADR-074**: [The Outbox Relay and Registry Honour Their Contracts](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)
* **arch42 §9.17**: Outbound Event Gateway and Transactional Outbox
* **arch42 §9.25**: Outbox Relay and Registry Contracts
