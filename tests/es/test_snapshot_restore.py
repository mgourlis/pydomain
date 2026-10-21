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
import enum
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar, Self
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    computed_field,
    model_validator,
)
from pydantic.alias_generators import to_camel
from pydantic.errors import PydanticUserError

from pydomain.ddd import DomainEvent
from pydomain.ddd.exceptions import DomainError
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.exceptions import SnapshotRestoreError
from pydomain.es.snapshot import (
    RejectStaleSnapshotPolicy,
    Snapshot,
    SnapshotThresholdPolicy,
)
from pydomain.testing import FakeEventStore, FakeSnapshotStore
from tests.es.conftest import LineItemAdded, OrderPlaced, TestOrder
from tests.support.content_sentinel import (
    CONTENT_SENTINEL,
    assert_absent_from_logs,
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


class PostedLine(BaseModel):
    """A frozen nested model, kept in a tuple."""

    model_config = ConfigDict(frozen=True)

    sku: str
    quantity: int


class Invoice(EventSourcedAggregateRoot[str]):
    """An invoice whose state holds nested models, as many aggregates do:
    a model, a list, an optional model, a dict of models, a tuple of frozen
    models and an aliased field."""

    address: BillingAddress = Field(default_factory=BillingAddress)
    lines: list[InvoiceLine] = Field(default_factory=list)
    last_line: InvoiceLine | None = None
    by_sku: dict[str, InvoiceLine] = Field(default_factory=dict)
    posted: tuple[PostedLine, ...] = ()
    reference: str = Field(default="", alias="invoiceRef")
    total_quantity: int = 0

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, InvoiceOpened):
            self.address = BillingAddress(street="1 Synthetic Way", city=event.city)
            self.reference = f"ref-{event.invoice_id}"
        elif isinstance(event, LineBilled):
            # Reads the nested models, as domain logic does.
            assert self.address.city
            line = InvoiceLine(sku=event.sku, quantity=event.quantity)
            self.lines.append(line)
            self.last_line = line
            self.by_sku[line.sku] = line
            self.posted = (
                *self.posted,
                PostedLine(sku=line.sku, quantity=line.quantity),
            )
            self.total_quantity = sum(line.quantity for line in self.lines)
        else:
            raise ValueError(f"Unknown event: {type(event).__name__}")


class JsonSnapshotStore:
    """A store that serializes snapshots to JSON text, as a database adapter
    does, and builds a new ``Snapshot`` on each ``get``."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None:
        self.rows[(aggregate_type, snapshot.aggregate_id)] = snapshot.model_dump_json()

    async def get(self, aggregate_type: str, aggregate_id: str) -> Snapshot | None:
        raw = self.rows.get((aggregate_type, aggregate_id))
        return None if raw is None else Snapshot.model_validate_json(raw)


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
        "last_line": (
            None
            if invoice.last_line is None
            else (invoice.last_line.sku, invoice.last_line.quantity)
        ),
        "by_sku": {sku: line.quantity for sku, line in invoice.by_sku.items()},
        "posted": [(line.sku, line.quantity) for line in invoice.posted],
        "posted_type": type(invoice.posted).__name__,
        "reference": invoice.reference,
        "total_quantity": invoice.total_quantity,
    }


# ===================================================================
# Snapshot plus tail equals full replay (ES-08)
# ===================================================================


@pytest.mark.parametrize("store_cls", [FakeSnapshotStore, JsonSnapshotStore])
@settings(report_multiple_bugs=False)
@given(
    quantities=st.lists(st.integers(min_value=1, max_value=50), max_size=8),
    data=st.data(),
)
def test_snapshot_plus_tail_equals_full_replay(
    store_cls: type[Any], quantities: list[int], data: st.DataObject
) -> None:
    """Loading from a snapshot at any version, then replaying the tail, gives
    the aggregate a full replay gives, nested models included (a model, a
    list, an optional model, a dict of models, a tuple of frozen models and an
    aliased field), with the in-memory fake and with a JSON round-trip store.

    A module-level function, not a method: Hypothesis rejects a test called
    through several class instances in one process (mutmut does).
    """
    events = _invoice_events("inv-1", quantities)
    snapshot_at = data.draw(st.integers(min_value=1, max_value=len(events)))

    async def scenario() -> None:
        event_store = FakeEventStore()
        await event_store.append_to_stream("inv-1", events, expected_version=0)
        snapshot_store = store_cls()
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
    store returned unchanged, whatever the store does about copies, for
    typed containers (validation builds new ones); a field typed ``Any``
    would still share its object with the snapshot."""
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


@pytest.mark.anyio
async def test_invalid_state_raises_snapshot_restore_error() -> None:
    """State that no longer validates raises ``SnapshotRestoreError`` with a
    value-free description, and no full replay is attempted (B-55)."""
    with pytest.raises(DomainError) as caught:
        await _load_from(Ledger, {"entries": [CONTENT_SENTINEL]}, [])

    error = caught.value
    assert type(error) is SnapshotRestoreError
    assert str(error) == (
        "Snapshot of Ledger 'agg-1' at version 2 could not be restored: "
        "ValidationError: 1 error for Ledger at entries.0 (int_parsing)"
    )
    assert error.aggregate_id == "agg-1"
    assert error.snapshot_version == 2


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


@pytest.mark.anyio
async def test_key_of_a_removed_field_is_invalid_with_extra_forbid() -> None:
    """An aggregate that forbids extra keys rejects a removed field's key as
    invalid state; unknown keys are not stripped (B-55)."""
    with pytest.raises(DomainError) as caught:
        await _load_from(StrictLedger, {"entries": [5], "legacy_discount": 1}, [])

    assert type(caught.value) is SnapshotRestoreError
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


# ===================================================================
# Aliased fields (PR #111, B-66)
# ===================================================================


class Counted(DomainEvent):
    aggregate: str
    quantity: int


async def _round_trip(aggregate: Any) -> Any:
    """Apply two events, save with a snapshot, then load from it."""
    event_store = FakeEventStore()
    repo = EventSourcedRepository(
        event_store,
        type(aggregate),
        snapshot_store=FakeSnapshotStore(),
        snapshot_policy=SnapshotThresholdPolicy(0),
    )
    for quantity in (1, 2):
        aggregate._apply(Counted(aggregate=aggregate.id, quantity=quantity))
    await repo.save(aggregate)
    return await repo.get_by_id(aggregate.id)


class AliasedTally(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, alias="totalQty")

    def _when(self, event: DomainEvent) -> None:
        assert isinstance(event, Counted)
        self.total += event.quantity


class RequiredAliasTally(EventSourcedAggregateRoot[str]):
    label: str = Field(alias="Label")

    def _when(self, event: DomainEvent) -> None:
        self.label += "+"


class ChoicesTally(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, validation_alias=AliasChoices("qty_total", "q"))

    def _when(self, event: DomainEvent) -> None:
        assert isinstance(event, Counted)
        self.total += event.quantity


class AliasedBin(BaseModel):
    quantity: int = Field(default=0, alias="Qty")


class NestedAliasTally(EventSourcedAggregateRoot[str]):
    bin: AliasedBin = Field(default_factory=AliasedBin)

    def _when(self, event: DomainEvent) -> None:
        assert isinstance(event, Counted)
        self.bin = AliasedBin(Qty=self.bin.quantity + event.quantity)


class DumpsByAliasTally(AliasedTally):
    model_config = ConfigDict(serialize_by_alias=True)


@pytest.mark.anyio
async def test_aliased_field_is_restored() -> None:
    """A field with an alias keeps its value through a snapshot (B-66)."""
    loaded = await _round_trip(AliasedTally(id="tally-1"))

    assert loaded.total == 3


@pytest.mark.anyio
async def test_required_aliased_field_is_restored() -> None:
    """A required field with an alias loads from its snapshot (B-66)."""
    loaded = await _round_trip(RequiredAliasTally(id="tally-2", Label="L"))

    assert loaded.label == "L++"


@pytest.mark.anyio
async def test_alias_choices_field_is_restored() -> None:
    """A field with ``AliasChoices`` keeps its value through a snapshot
    (B-66)."""
    loaded = await _round_trip(ChoicesTally(id="tally-3"))

    assert loaded.total == 3


@pytest.mark.anyio
async def test_nested_aliased_field_is_restored() -> None:
    """An aliased field of a nested model keeps its value (B-66)."""
    loaded = await _round_trip(NestedAliasTally(id="tally-4"))

    assert loaded.bin.quantity == 3


def test_snapshot_state_uses_field_names_with_serialize_by_alias() -> None:
    """The snapshot state is keyed by field names even when the model dumps
    by alias, so stores see one shape (B-66)."""
    tally = DumpsByAliasTally(id="tally-5")
    tally._apply(Counted(aggregate="tally-5", quantity=4))

    assert tally._take_snapshot().state == {"id": "tally-5", "total": 4}


# ===================================================================
# Computed fields of nested models (PR #111, B-66)
# ===================================================================


class StrictCrate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: list[int] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return sum(self.weights)


class CrateHolder(EventSourcedAggregateRoot[str]):
    crate: StrictCrate = Field(default_factory=StrictCrate)

    def _when(self, event: DomainEvent) -> None:
        assert isinstance(event, Counted)
        self.crate.weights.append(event.quantity)


def test_take_snapshot_excludes_nested_computed_fields() -> None:
    """Computed fields are left out at every depth (B-66)."""
    holder = CrateHolder(id="crate-1")
    holder._apply(Counted(aggregate="crate-1", quantity=2))

    assert holder._take_snapshot().state == {"id": "crate-1", "crate": {"weights": [2]}}


@pytest.mark.anyio
async def test_nested_computed_field_with_extra_forbid_reloads() -> None:
    """A nested model with a computed field and ``extra="forbid"`` loads from
    its snapshot (B-66)."""
    loaded = await _round_trip(CrateHolder(id="crate-2"))

    assert loaded.crate.weights == [1, 2]
    assert loaded.crate.total == 3


# ===================================================================
# Any exception during restore (PR #111, B-67)
# ===================================================================


class _Interrupt(BaseException):
    """A BaseException that is not an Exception, like KeyboardInterrupt."""


class FaultyNote(EventSourcedAggregateRoot[str]):
    """Injects a failure into the restore: a model validator or
    ``model_post_init`` raises the class's ``fault`` once ``note`` is set."""

    fault_in: ClassVar[str] = ""
    fault: ClassVar[type[BaseException]] = RuntimeError

    note: str = ""

    @model_validator(mode="after")
    def _check_note(self) -> Self:
        if self.note and self.fault_in == "validator":
            raise self.fault(f"note {self.note!r} breaks a rule")
        return self

    def model_post_init(self, context: Any, /) -> None:
        if self.note and self.fault_in == "post_init":
            raise self.fault(f"cannot index note {self.note!r}")

    def _when(self, event: DomainEvent) -> None:
        pass


def _faulty(where: str, fault: type[BaseException]) -> type[FaultyNote]:
    namespace = {"__module__": __name__, "fault_in": where, "fault": fault}
    return type("FaultyNote", (FaultyNote,), namespace)


def _b67_case(where: str, fault: type[BaseException]) -> Any:
    return pytest.param(where, fault, id=f"{where}-{fault.__name__}")


@pytest.mark.parametrize(
    ("where", "fault"),
    [
        _b67_case("validator", DomainError),
        _b67_case("post_init", KeyError),
        _b67_case("post_init", RuntimeError),
        _b67_case("post_init", DomainError),
        pytest.param("validator", ValueError, id="validator-ValueError"),
    ],
)
@pytest.mark.anyio
async def test_any_exception_during_restore_raises_snapshot_restore_error(
    where: str, fault: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    """Any ``Exception`` raised while the state is validated, by a model
    validator or by ``model_post_init``, becomes a ``SnapshotRestoreError``
    named by type only, with no cause and no context (B-67)."""
    with pytest.raises(SnapshotRestoreError) as caught:
        await _load_from(_faulty(where, fault), {"note": CONTENT_SENTINEL}, [])

    error = caught.value
    expected = "ValidationError" if fault is ValueError else fault.__name__
    assert expected in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    logging.getLogger("tests.snapshot").exception("load failed: %s", error)
    assert_absent_from_reprs(error)
    assert_absent_from_logs(caplog)


@pytest.mark.anyio
async def test_base_exception_during_restore_propagates() -> None:
    """A ``BaseException`` that is not an ``Exception`` is not wrapped
    (B-67)."""
    with pytest.raises(_Interrupt):
        await _load_from(_faulty("post_init", _Interrupt), {"note": "x"}, [])


# ===================================================================
# The version is part of the validated state (PR #111)
# ===================================================================


class VersionWatcher(EventSourcedAggregateRoot[str]):
    """Records the version each run of its model validator sees."""

    seen: ClassVar[list[int]] = []

    total: int = 0

    @model_validator(mode="after")
    def _record_version(self) -> Self:
        type(self).seen.append(self.version)
        return self

    def _when(self, event: DomainEvent) -> None:
        pass


class AssignmentVersionWatcher(VersionWatcher):
    model_config = ConfigDict(validate_assignment=True)

    seen: ClassVar[list[int]] = []


@pytest.mark.anyio
async def test_validators_see_the_snapshot_version() -> None:
    """The aggregate's validators see the snapshot's version, not 0."""
    VersionWatcher.seen.clear()

    await _load_from(VersionWatcher, {"total": 1}, [])

    assert VersionWatcher.seen == [2]


@pytest.mark.anyio
async def test_validate_assignment_does_not_run_validators_twice_on_restore() -> None:
    """With ``validate_assignment``, a restore still validates once."""
    AssignmentVersionWatcher.seen.clear()

    await _load_from(AssignmentVersionWatcher, {"total": 1}, [])

    assert AssignmentVersionWatcher.seen == [2]


# ===================================================================
# Content sentinel across invalid shapes (PR #111)
# ===================================================================


class SentinelLine(BaseModel):
    sku: str
    quantity: int


class SentinelOrder(EventSourcedAggregateRoot[str]):
    lines: list[SentinelLine] = Field(default_factory=list)
    count: int = 0
    tags: dict[str, int] = Field(default_factory=dict)

    def _when(self, event: DomainEvent) -> None:
        pass


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(
            {"lines": [{"sku": "x", "quantity": CONTENT_SENTINEL}]},
            id="nested-str-in-int",
        ),
        pytest.param({"count": CONTENT_SENTINEL}, id="top-level-str-in-int"),
        pytest.param(
            {"lines": [{"sku": CONTENT_SENTINEL}]},
            id="missing-nested-field-beside-a-value",
        ),
        pytest.param({"lines": CONTENT_SENTINEL}, id="wrong-container"),
        pytest.param({"tags": {"k": CONTENT_SENTINEL}}, id="dict-value"),
    ],
)
@pytest.mark.anyio
async def test_snapshot_restore_error_keeps_values_out_of_reprs_and_logs(
    state: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Whatever shape fails, no stored value reaches the error, its
    traceback or a log line that formats it (B-55, ADR-071)."""
    with pytest.raises(SnapshotRestoreError) as caught:
        await _load_from(SentinelOrder, state, [])

    logging.getLogger("tests.snapshot").exception("load failed: %s", caught.value)
    assert_absent_from_reprs(caught.value)
    assert_absent_from_logs(caplog)


@pytest.mark.anyio
async def test_dict_key_appears_in_the_error_location() -> None:
    """A dict field's key is part of the failing location, so it does reach
    the message: locations include dict keys, as ADR-071 allows (B-30)."""
    with pytest.raises(SnapshotRestoreError) as caught:
        await _load_from(SentinelOrder, {"tags": {CONTENT_SENTINEL: "x"}}, [])

    assert f"tags.{CONTENT_SENTINEL} (int_parsing)" in str(caught.value)


# ===================================================================
# What the docs promise about private attributes and coercion (PR #111)
# ===================================================================


class DeriveOnlyLedger(EventSourcedAggregateRoot[str]):
    """Derives a private attribute in ``model_post_init`` only."""

    entries: list[int] = Field(default_factory=list)
    _entry_count: int = PrivateAttr(default=0)

    def model_post_init(self, context: Any, /) -> None:
        self._entry_count = len(self.entries)

    @property
    def entry_count(self) -> int:
        return self._entry_count

    def _when(self, event: DomainEvent) -> None:
        assert isinstance(event, LineBilled)
        self.entries.append(event.quantity)  # _entry_count not kept current


@pytest.mark.anyio
async def test_private_attribute_derived_only_in_post_init_depends_on_load_path() -> (
    None
):
    """A private attribute derived in ``model_post_init`` but not kept
    current in ``_when`` reflects the snapshot on the snapshot path and the
    empty aggregate on a full replay: derive it and keep it current (B-54)."""
    from_snapshot = await _load_from(DeriveOnlyLedger, {"entries": [1, 2]}, [3])
    event_store = FakeEventStore()
    events: list[DomainEvent] = [
        LineBilled(invoice_id="agg-1", sku="sku", quantity=quantity)
        for quantity in (1, 2, 3)
    ]
    await event_store.append_to_stream("agg-1", events, expected_version=0)
    replayed = await EventSourcedRepository(event_store, DeriveOnlyLedger).get_by_id(
        "agg-1"
    )

    assert replayed is not None
    assert from_snapshot.entries == replayed.entries == [1, 2, 3]
    assert from_snapshot.entry_count == 2
    assert replayed.entry_count == 0


@pytest.mark.anyio
async def test_compatible_value_is_coerced_on_restore() -> None:
    """A value that still validates is coerced, as Pydantic's lax mode does:
    a stored ``"5"`` loads into an ``int`` field as 5."""
    loaded = await _load_from(SentinelOrder, {"count": "5"}, [])

    assert loaded.count == 5


class LedgerWithNewField(EventSourcedAggregateRoot[str]):
    entries: list[int] = Field(default_factory=list)
    region: str

    def _when(self, event: DomainEvent) -> None:
        pass


@pytest.mark.anyio
async def test_new_required_field_without_default_raises() -> None:
    """A snapshot taken before a required field without a default was added
    raises ``SnapshotRestoreError`` naming the missing field."""
    with pytest.raises(SnapshotRestoreError, match=r"region \(missing\)"):
        await _load_from(LedgerWithNewField, {"entries": [1]}, [])


class NameOnlyTally(AliasedTally):
    model_config = ConfigDict(validate_by_alias=False, validate_by_name=True)


@pytest.mark.parametrize("aggregate_cls", [AliasedTally, NameOnlyTally])
@pytest.mark.anyio
async def test_state_keyed_by_alias_still_loads(aggregate_cls: type[Any]) -> None:
    """A snapshot keyed by aliases (taken before B-66 by a model that
    serialized by alias) loads too: the restore accepts aliases whatever
    the model's own ``validate_by_alias`` says (B-66)."""
    loaded = await _load_from(aggregate_cls, {"totalQty": 3}, [])

    assert loaded.total == 3


# ===================================================================
# Aliases that collide with field names (PR #111 round 2, B-74)
# ===================================================================


class CollidingTally(EventSourcedAggregateRoot[str]):
    """Field ``first``'s alias is the name of field ``second``."""

    first: int = Field(default=0, alias="second")
    second: int = 0

    def _when(self, event: DomainEvent) -> None:
        self.first, self.second = 1, 2


class SwappedTally(EventSourcedAggregateRoot[str]):
    """Each field's alias is the other field's name."""

    model_config = ConfigDict(populate_by_name=True)

    first: int = Field(default=0, alias="second")
    second: int = Field(default=0, alias="first")

    def _when(self, event: DomainEvent) -> None:
        self.first, self.second = 1, 2


class NameOnlyCollidingTally(CollidingTally):
    model_config = ConfigDict(validate_by_alias=False, validate_by_name=True)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-119 PR #111 B-74: an alias equal to a field name wins",
)
@pytest.mark.parametrize(
    "aggregate_cls", [CollidingTally, SwappedTally, NameOnlyCollidingTally]
)
@pytest.mark.anyio
async def test_alias_equal_to_another_field_name_keeps_values(
    aggregate_cls: type[Any],
) -> None:
    """State keyed by field names restores each field from its own key,
    even when an alias equals another field's name (B-74)."""
    loaded = await _round_trip(aggregate_cls(id="tally-6"))

    assert (loaded.first, loaded.second) == (1, 2)


class SerializationAliasLedger(EventSourcedAggregateRoot[str]):
    """Dumped by a serialization-only alias, as 0.3.0 snapshotted it."""

    model_config = ConfigDict(serialize_by_alias=True)

    balance: int = Field(default=0, serialization_alias="Balance")

    def _when(self, event: DomainEvent) -> None:
        pass


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-119 PR #111 B-74: a serialization-alias key is ignored",
)
@pytest.mark.anyio
async def test_state_keyed_by_serialization_alias_still_loads() -> None:
    """An old snapshot keyed by a serialization-only alias loads: the key is
    renamed to the field name before validation (B-74)."""
    loaded = await _load_from(SerializationAliasLedger, {"Balance": 42}, [])

    assert loaded.balance == 42


# ===================================================================
# Probes from the second review that pass (PR #111 round 2)
# ===================================================================


class CamelTally(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(
        alias_generator=to_camel, serialize_by_alias=True, validate_by_name=False
    )

    order_total: int = 0

    def _when(self, event: DomainEvent) -> None:
        assert isinstance(event, Counted)
        self.order_total += event.quantity


class ValidationAliasTally(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, validation_alias="TOTAL")

    def _when(self, event: DomainEvent) -> None:
        assert isinstance(event, Counted)
        self.total += event.quantity


class AliasPathTally(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, validation_alias=AliasPath("wrap", 0))

    def _when(self, event: DomainEvent) -> None:
        assert isinstance(event, Counted)
        self.total += event.quantity


@pytest.mark.parametrize(
    "aggregate_cls", [CamelTally, ValidationAliasTally, AliasPathTally]
)
@pytest.mark.anyio
async def test_generated_validation_and_path_aliases_round_trip(
    aggregate_cls: type[Any],
) -> None:
    """``alias_generator`` (with ``validate_by_name=False``), a
    ``validation_alias`` and an ``AliasPath`` keep their values through a
    snapshot keyed by field names (B-66, B-74)."""
    aggregate = aggregate_cls(id="tally-7")
    snapshot_fields = set(aggregate._take_snapshot().state)
    loaded = await _round_trip(aggregate)

    assert snapshot_fields == {"id", *aggregate_cls.model_fields} - {"version"}
    assert loaded.model_dump(by_alias=False) == aggregate.model_dump(by_alias=False)


@pytest.mark.anyio
async def test_state_keyed_by_generated_alias_still_loads() -> None:
    """An old snapshot keyed by ``alias_generator`` aliases loads (B-74)."""
    loaded = await _load_from(CamelTally, {"orderTotal": 9}, [])

    assert loaded.order_total == 9


class Tint(enum.Enum):
    RED = "red"


class StrictRecord(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(strict=True)

    at: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    ref: UUID = UUID(int=7)
    tint: Tint = Tint.RED
    amount: Decimal = Decimal("1.10")
    pair: tuple[int, int] = (1, 2)
    labels: set[str] = Field(default_factory=lambda: {"a"})

    def _when(self, event: DomainEvent) -> None:
        pass


@pytest.mark.anyio
async def test_strict_aggregate_through_a_json_store_raises() -> None:
    """A strict-mode aggregate cannot be restored through a store that
    serializes to JSON: datetime, UUID, Enum, Decimal, tuple and set values
    come back as JSON types, which strict mode refuses (documented)."""
    snapshot_store = JsonSnapshotStore()
    await snapshot_store.save("StrictRecord", StrictRecord(id="rec-1")._take_snapshot())
    repo = EventSourcedRepository(
        FakeEventStore(), StrictRecord, snapshot_store=snapshot_store
    )

    with pytest.raises(SnapshotRestoreError) as caught:
        await repo.get_by_id("rec-1")

    for location in ("at", "ref", "tint", "amount", "pair", "labels"):
        assert f" {location} (" in str(caught.value)


class OldCrateHolder(EventSourcedAggregateRoot[str]):
    crate: StrictCrate = Field(default_factory=StrictCrate)

    def _when(self, event: DomainEvent) -> None:
        pass


@pytest.mark.anyio
async def test_old_snapshot_with_nested_computed_key_under_forbid_raises() -> None:
    """Only top-level computed keys are stripped: an old snapshot holding a
    nested model's computed key under that model's ``extra="forbid"`` raises;
    bump ``_snapshot_schema_version`` to replay instead (documented, S7)."""
    with pytest.raises(SnapshotRestoreError, match=r"crate\.total \(extra_forbidden\)"):
        await _load_from(OldCrateHolder, {"crate": {"weights": [1], "total": 1}}, [])


class UndefinedForwardRef(EventSourcedAggregateRoot[str]):
    """A class definition error: the annotation never resolves."""

    later: Undefined | None = None  # type: ignore[name-defined]  # noqa: F821

    def _when(self, event: DomainEvent) -> None:
        pass


class MemoryHungry(EventSourcedAggregateRoot[str]):
    def model_post_init(self, context: Any, /) -> None:
        if self.version:
            raise MemoryError

    def _when(self, event: DomainEvent) -> None:
        pass


@pytest.mark.anyio
async def test_class_definition_error_surfaces_by_type_name() -> None:
    """A broken class surfaces as ``SnapshotRestoreError`` named by type
    only; constructing the class directly shows the real message."""
    with pytest.raises(SnapshotRestoreError, match=r"restored: PydanticUserError$"):
        await _load_from(UndefinedForwardRef, {}, [])
    with pytest.raises(PydanticUserError, match="not fully defined"):
        UndefinedForwardRef(id="direct")


@pytest.mark.anyio
async def test_memory_error_in_post_init_is_wrapped() -> None:
    """``MemoryError`` is an ``Exception``, so it is wrapped too (B-67)."""
    with pytest.raises(SnapshotRestoreError, match=r"restored: MemoryError$"):
        await _load_from(MemoryHungry, {}, [])
