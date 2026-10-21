"""Tests for restoring an aggregate from a snapshot (W1-4, ADR-075).

``EventSourcedRepository.get_by_id()`` rebuilds an aggregate from its latest
snapshot plus the events after it. These tests pin that the result is the same
aggregate a full replay builds: nested models come back as models, a computed
field does not break the load, and loading never changes the stored snapshot.
They also pin the decisions B-53 to B-56: the aggregate's validators run on the
restored state, private attributes start from their defaults, state that no
longer validates raises a value-free ``SnapshotRestoreError``, and computed
keys in old snapshots are stripped.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Self

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    computed_field,
    model_validator,
)

import pydomain.es.exceptions as es_exceptions
from pydomain.ddd import DomainEvent
from pydomain.ddd.exceptions import DomainError
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import (
    RejectStaleSnapshotPolicy,
    Snapshot,
    SnapshotThresholdPolicy,
)
from pydomain.testing import FakeEventStore, FakeSnapshotStore
from tests.es.conftest import LineItemAdded, OrderPlaced, TestOrder
from tests.support.content_sentinel import (
    CONTENT_SENTINEL,
    assert_absent_from_reprs,
)

# ---- Synthetic domain with nested models ----


class InvoiceOpened(DomainEvent):
    invoice_id: str
    city: str


class LineBilled(DomainEvent):
    invoice_id: str
    sku: str
    quantity: int


class BillingAddress(BaseModel):
    street: str = ""
    city: str = ""


class InvoiceLine(BaseModel):
    sku: str
    quantity: int


class Invoice(EventSourcedAggregateRoot[str]):
    """An invoice whose state holds nested models, as many aggregates do."""

    address: BillingAddress = Field(default_factory=BillingAddress)
    lines: list[InvoiceLine] = Field(default_factory=list)
    total_quantity: int = 0

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, InvoiceOpened):
            self.address = BillingAddress(street="1 Synthetic Way", city=event.city)
        elif isinstance(event, LineBilled):
            # Reads the nested models, as domain logic does.
            assert self.address.city
            self.lines.append(InvoiceLine(sku=event.sku, quantity=event.quantity))
            self.total_quantity = sum(line.quantity for line in self.lines)
        else:
            raise ValueError(f"Unknown event: {type(event).__name__}")


class Shipment(EventSourcedAggregateRoot[str]):
    """An aggregate with a computed field."""

    parcels: list[int] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def parcel_count(self) -> int:
        return len(self.parcels)

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, LineBilled):
            self.parcels.append(event.quantity)
        else:
            raise ValueError(f"Unknown event: {type(event).__name__}")


def _invoice_events(invoice_id: str, quantities: list[int]) -> list[DomainEvent]:
    events: list[DomainEvent] = [InvoiceOpened(invoice_id=invoice_id, city="Testville")]
    events.extend(
        LineBilled(invoice_id=invoice_id, sku=f"sku-{n}", quantity=quantity)
        for n, quantity in enumerate(quantities)
    )
    return events


def _invoice_state(invoice: Invoice) -> dict[str, Any]:
    """The invoice's fields as objects, read through the nested models."""
    return {
        "id": invoice.id,
        "version": invoice.version,
        "address": (invoice.address.street, invoice.address.city),
        "lines": [(line.sku, line.quantity) for line in invoice.lines],
        "total_quantity": invoice.total_quantity,
    }


# ===================================================================
# Snapshot plus tail equals full replay (ES-08)
# ===================================================================


@settings(report_multiple_bugs=False)
@given(
    quantities=st.lists(st.integers(min_value=1, max_value=50), max_size=8),
    data=st.data(),
)
def test_snapshot_plus_tail_equals_full_replay(
    quantities: list[int], data: st.DataObject
) -> None:
    """Loading from a snapshot at any version, then replaying the tail, gives
    the aggregate a full replay gives, nested models included.

    A module-level function, not a method: Hypothesis rejects a test called
    through several class instances in one process (mutmut does).
    """
    events = _invoice_events("inv-1", quantities)
    snapshot_at = data.draw(st.integers(min_value=1, max_value=len(events)))

    async def scenario() -> None:
        event_store = FakeEventStore()
        await event_store.append_to_stream("inv-1", events, expected_version=0)
        snapshot_store = FakeSnapshotStore()
        prefix = Invoice(id="inv-1")
        for event in events[:snapshot_at]:
            prefix._replay(event)
        await snapshot_store.save("Invoice", prefix._take_snapshot())

        with_snapshot = await EventSourcedRepository(
            event_store, Invoice, snapshot_store=snapshot_store
        ).get_by_id("inv-1")
        full_replay = await EventSourcedRepository(event_store, Invoice).get_by_id(
            "inv-1"
        )

        assert with_snapshot is not None
        assert full_replay is not None
        assert _invoice_state(with_snapshot) == _invoice_state(full_replay)

    asyncio.run(scenario())


@pytest.mark.anyio
async def test_nested_models_are_restored_as_models() -> None:
    """A nested model and a list of nested models come back as their model
    types, not as the dicts the snapshot stores."""
    event_store = FakeEventStore()
    snapshot_store = FakeSnapshotStore()
    repo = EventSourcedRepository(
        event_store,
        Invoice,
        snapshot_store=snapshot_store,
        snapshot_policy=SnapshotThresholdPolicy(0),
    )
    invoice = Invoice(id="inv-2")
    for event in _invoice_events("inv-2", [3, 4]):
        invoice._apply(event)
    await repo.save(invoice)

    loaded = await repo.get_by_id("inv-2")

    assert loaded is not None
    assert isinstance(loaded.address, BillingAddress)
    assert all(isinstance(line, InvoiceLine) for line in loaded.lines)


# ===================================================================
# Repeated loads (ES-09)
# ===================================================================


@pytest.mark.anyio
async def test_repeated_loads_are_identical() -> None:
    """Three loads from the same snapshot and tail give three equal
    aggregates, equal to a full replay; no load adds the tail again."""
    event_store = FakeEventStore()
    snapshot_store = FakeSnapshotStore()
    repo: EventSourcedRepository[TestOrder, str] = EventSourcedRepository(
        event_store,
        TestOrder,
        snapshot_store=snapshot_store,
        snapshot_policy=SnapshotThresholdPolicy(2),
    )
    order = TestOrder(id="order-1")
    order._apply(OrderPlaced(order_id="order-1", customer_name="Synthetic"))
    order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=1.0))
    await repo.save(order)  # version 2: snapshot taken
    order._apply(LineItemAdded(order_id="order-1", item_name="Gadget", price=2.0))
    await repo.save(order)  # version 3: no snapshot, one tail event

    loads = [await repo.get_by_id("order-1") for _ in range(3)]
    full_replay = await EventSourcedRepository(event_store, TestOrder).get_by_id(
        "order-1"
    )

    assert full_replay is not None
    expected = full_replay.model_dump()
    assert [load.model_dump() for load in loads if load is not None] == [expected] * 3


class _SharedSnapshotStore:
    """A store that hands out the very snapshot it holds, as a cache might."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    async def get(self, aggregate_type: str, aggregate_id: str) -> Snapshot | None:
        return self.snapshot


@pytest.mark.anyio
async def test_load_does_not_change_the_snapshot_it_restores_from() -> None:
    """Replaying the tail onto a restored aggregate leaves the snapshot the
    store returned unchanged, whatever the store does about copies."""
    event_store = FakeEventStore()
    order = TestOrder(id="order-2")
    order._apply(OrderPlaced(order_id="order-2", customer_name="Synthetic"))
    order._apply(LineItemAdded(order_id="order-2", item_name="Widget", price=1.0))
    snapshot = order._take_snapshot()
    order._apply(LineItemAdded(order_id="order-2", item_name="Gadget", price=2.0))
    await event_store.append_to_stream("order-2", order.pull_events(), 0)
    store = _SharedSnapshotStore(snapshot)
    before = snapshot.model_dump()

    loaded = await EventSourcedRepository(
        event_store, TestOrder, snapshot_store=store
    ).get_by_id("order-2")

    assert loaded is not None
    assert len(loaded.items) == 2
    assert store.snapshot.model_dump() == before


# ===================================================================
# Computed fields (ES-08)
# ===================================================================


@pytest.mark.anyio
async def test_computed_field_does_not_break_snapshot_load() -> None:
    """An aggregate with a computed field loads from its snapshot, and the
    computed field reflects the restored state."""
    event_store = FakeEventStore()
    snapshot_store = FakeSnapshotStore()
    repo = EventSourcedRepository(
        event_store,
        Shipment,
        snapshot_store=snapshot_store,
        snapshot_policy=SnapshotThresholdPolicy(2),
    )
    shipment = Shipment(id="ship-1")
    for quantity in (1, 2, 3):
        shipment._apply(LineBilled(invoice_id="ship-1", sku="box", quantity=quantity))
    await repo.save(shipment)  # version 3: no snapshot
    shipment._apply(LineBilled(invoice_id="ship-1", sku="box", quantity=4))
    await repo.save(shipment)  # version 4: snapshot taken

    loaded = await repo.get_by_id("ship-1")

    assert loaded is not None
    assert loaded.parcels == [1, 2, 3, 4]
    assert loaded.parcel_count == 4


def test_take_snapshot_excludes_computed_fields() -> None:
    """A computed field is derived state: the snapshot does not store it."""
    shipment = Shipment(id="ship-2")
    shipment._apply(LineBilled(invoice_id="ship-2", sku="box", quantity=1))

    snapshot = shipment._take_snapshot()

    assert "parcel_count" not in snapshot.state
    assert snapshot.state["parcels"] == [1]


# ===================================================================
# Validators, private attributes and invalid state (B-53 to B-56)
# ===================================================================


class Ledger(EventSourcedAggregateRoot[str]):
    """An aggregate with a model validator and a derived private attribute."""

    currency: str = "EUR"
    entries: list[int] = Field(default_factory=list)
    _balance: int = PrivateAttr(default=0)

    @model_validator(mode="after")
    def _normalise_currency(self) -> Self:
        self.currency = self.currency.upper()
        return self

    def model_post_init(self, context: Any, /) -> None:
        self._balance = sum(self.entries)

    @property
    def balance(self) -> int:
        return self._balance

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, LineBilled):
            self.entries.append(event.quantity)
            self._balance += event.quantity
        else:
            raise ValueError(f"Unknown event: {type(event).__name__}")


class StrictParcel(EventSourcedAggregateRoot[str]):
    """An aggregate that forbids unknown keys and has a computed field."""

    model_config = ConfigDict(extra="forbid")

    weights: list[int] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def weight(self) -> int:
        return sum(self.weights)

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, LineBilled):
            self.weights.append(event.quantity)
        else:
            raise ValueError(f"Unknown event: {type(event).__name__}")


async def _load_from(
    aggregate_cls: type[Any], state: dict[str, Any], tail: list[int]
) -> Any:
    """Store a snapshot at version 2 with *state*, append *tail*, then load."""
    event_store = FakeEventStore()
    events: list[DomainEvent] = [
        LineBilled(invoice_id="agg-1", sku="sku", quantity=quantity)
        for quantity in [0, 0, *tail]
    ]
    await event_store.append_to_stream("agg-1", events, expected_version=0)
    snapshot_store = FakeSnapshotStore()
    await snapshot_store.save(
        aggregate_cls.__name__,
        Snapshot(aggregate_id="agg-1", version=2, state=state),
    )
    repo = EventSourcedRepository(
        event_store, aggregate_cls, snapshot_store=snapshot_store
    )
    return await repo.get_by_id("agg-1")


@pytest.mark.anyio
async def test_model_validators_run_on_the_restored_state() -> None:
    """The aggregate's model validators see the snapshot's state (B-53)."""
    loaded = await _load_from(Ledger, {"currency": "usd", "entries": [1]}, [])

    assert loaded.currency == "USD"


@pytest.mark.anyio
async def test_private_attributes_are_derived_in_model_post_init() -> None:
    """Private attributes are not in the snapshot; they start from their
    defaults and ``model_post_init`` derives them from the restored state
    (B-54)."""
    loaded = await _load_from(Ledger, {"entries": [1, 2]}, [3])

    assert loaded.entries == [1, 2, 3]
    assert loaded.balance == 6


def test_private_attributes_are_not_snapshot_state() -> None:
    """A snapshot holds fields only, never private attributes (B-54)."""
    ledger = Ledger(id="agg-2")
    ledger._apply(LineBilled(invoice_id="agg-2", sku="sku", quantity=4))

    snapshot = ledger._take_snapshot()

    assert set(snapshot.state) == {"id", "currency", "entries"}


@pytest.mark.anyio
async def test_key_of_a_removed_field_is_ignored() -> None:
    """A snapshot taken before a field was removed still loads (B-55)."""
    loaded = await _load_from(Ledger, {"entries": [5], "legacy_discount": 1}, [])

    assert loaded.entries == [5]


@pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason="DCE-119 B-55: the raw ValidationError reaches the caller",
)
@pytest.mark.anyio
async def test_invalid_state_raises_snapshot_restore_error() -> None:
    """State that no longer validates raises ``SnapshotRestoreError`` with a
    value-free description, and no full replay is attempted (B-55)."""
    with pytest.raises(DomainError) as caught:
        await _load_from(Ledger, {"entries": [CONTENT_SENTINEL]}, [])

    error = caught.value
    assert type(error) is es_exceptions.SnapshotRestoreError  # type: ignore[attr-defined]
    assert str(error) == (
        "Snapshot of Ledger 'agg-1' at version 2 could not be restored: "
        "ValidationError: 1 error for Ledger at entries.0 (int_parsing)"
    )
    assert error.aggregate_id == "agg-1"  # type: ignore[attr-defined]
    assert error.snapshot_version == 2  # type: ignore[attr-defined]


@pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason="DCE-119 B-55: the raw ValidationError reaches the caller",
)
@pytest.mark.anyio
async def test_snapshot_restore_error_carries_no_payload_values() -> None:
    """Neither the error, its traceback nor any chained exception shows a
    stored value: it is raised with no cause and no context (B-55)."""
    with pytest.raises(DomainError) as caught:
        await _load_from(Ledger, {"entries": [CONTENT_SENTINEL]}, [])

    error = caught.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert_absent_from_reprs(error)


class StrictLedger(Ledger):
    model_config = ConfigDict(extra="forbid")


@pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason="DCE-119 B-55: the raw ValidationError reaches the caller",
)
@pytest.mark.anyio
async def test_key_of_a_removed_field_is_invalid_with_extra_forbid() -> None:
    """An aggregate that forbids extra keys rejects a removed field's key as
    invalid state; unknown keys are not stripped (B-55)."""
    with pytest.raises(DomainError) as caught:
        await _load_from(StrictLedger, {"entries": [5], "legacy_discount": 1}, [])

    assert type(caught.value) is es_exceptions.SnapshotRestoreError  # type: ignore[attr-defined]
    assert "legacy_discount (extra_forbidden)" in str(caught.value)


@pytest.mark.anyio
async def test_old_snapshot_with_a_computed_key_loads_with_extra_forbid() -> None:
    """A snapshot taken before computed fields were left out still loads:
    the restore strips computed keys, even when the aggregate forbids
    unknown keys (B-56)."""
    loaded = await _load_from(StrictParcel, {"weights": [2], "weight": 2}, [3])

    assert loaded.weights == [2, 3]
    assert loaded.weight == 5


# ===================================================================
# What the repository passes to its policies
# ===================================================================


class _SpySnapshotPolicy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def should_snapshot(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return False


class _SpySchemaPolicy(RejectStaleSnapshotPolicy):
    def __init__(self) -> None:
        self.expected: list[int] = []

    def should_use_snapshot(
        self, snapshot: Snapshot, expected_schema_version: int
    ) -> bool:
        self.expected.append(expected_schema_version)
        return super().should_use_snapshot(snapshot, expected_schema_version)


class _VersionThreeOrder(TestOrder):
    _snapshot_schema_version: ClassVar[int] = 3


@pytest.mark.anyio
async def test_save_passes_the_aggregate_type_and_id_to_the_policy() -> None:
    """``should_snapshot`` gets the repository's aggregate type, the
    aggregate id, the new version and the batch size."""
    policy = _SpySnapshotPolicy()
    repo: EventSourcedRepository[TestOrder, str] = EventSourcedRepository(
        FakeEventStore(),
        TestOrder,
        snapshot_store=FakeSnapshotStore(),
        snapshot_policy=policy,  # type: ignore[arg-type]
    )
    order = TestOrder(id="order-9")
    order._apply(OrderPlaced(order_id="order-9", customer_name="Synthetic"))
    order._apply(LineItemAdded(order_id="order-9", item_name="Widget", price=1.0))

    await repo.save(order)

    assert policy.calls == [
        {
            "aggregate_type": "TestOrder",
            "aggregate_id": "order-9",
            "current_version": 2,
            "pending_event_count": 2,
        }
    ]


@pytest.mark.anyio
async def test_schema_policy_gets_the_aggregate_schema_version() -> None:
    """``should_use_snapshot`` gets the aggregate class's
    ``_snapshot_schema_version`` as the expected version."""
    event_store = FakeEventStore()
    snapshot_store = FakeSnapshotStore()
    policy = _SpySchemaPolicy()
    order = _VersionThreeOrder(id="order-10")
    order._apply(OrderPlaced(order_id="order-10", customer_name="Synthetic"))
    await event_store.append_to_stream("order-10", order.pull_events(), 0)
    await snapshot_store.save("_VersionThreeOrder", order._take_snapshot())
    repo = EventSourcedRepository(
        event_store,
        _VersionThreeOrder,
        snapshot_store=snapshot_store,
        snapshot_schema_policy=policy,
    )

    loaded = await repo.get_by_id("order-10")

    assert policy.expected == [3]
    assert loaded is not None
    assert loaded.version == 1
