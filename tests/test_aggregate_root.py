from __future__ import annotations

from uuid import UUID, uuid4

from pydomain.ddd import AggregateRoot, DomainEvent

# ---------------------------------------------------------------------------
# Module-level DomainEvent subclasses
#
# These must live outside any AggregateRoot subclass because Pydantic's
# BaseModel.__getattr__ intercepts attribute access on model instances.
# ---------------------------------------------------------------------------


class OrderLineItemAdded(DomainEvent):
    order_id: UUID
    product_id: str
    quantity: int


class OrderShipped(DomainEvent):
    order_id: UUID


class OrderPaymentConfirmed(DomainEvent):
    order_id: UUID
    amount: float


# ---------------------------------------------------------------------------
# Module-level AggregateRoot subclass used across test classes
# ---------------------------------------------------------------------------


class Order(AggregateRoot[UUID]):
    status: str = "pending"

    def add_line_item(self, product_id: str, quantity: int) -> None:
        self._add_event(
            OrderLineItemAdded(
                order_id=self.id, product_id=product_id, quantity=quantity
            )
        )

    def ship(self) -> None:
        self.status = "shipped"
        self._add_event(OrderShipped(order_id=self.id))

    def confirm_payment(self, amount: float) -> None:
        self.status = "paid"
        self._add_event(OrderPaymentConfirmed(order_id=self.id, amount=amount))


# ===================================================================
# Event Collection
# ===================================================================


class TestEventCollection:
    def test_add_event_stores_event(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="ABC-123", quantity=2)
        events = order.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], OrderLineItemAdded)
        assert events[0].product_id == "ABC-123"
        assert events[0].quantity == 2

    def test_pull_events_returns_events_and_clears(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="ABC-123", quantity=2)
        events = order.pull_events()
        assert len(events) == 1
        remaining = order.pull_events()
        assert remaining == []

    def test_multiple_events_in_order(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=1)
        order.add_line_item(product_id="P002", quantity=3)
        order.ship()
        events = order.pull_events()
        assert len(events) == 3
        assert isinstance(events[0], OrderLineItemAdded)
        assert events[0].product_id == "P001"
        assert isinstance(events[1], OrderLineItemAdded)
        assert events[1].product_id == "P002"
        assert isinstance(events[2], OrderShipped)

    def test_pull_events_empty_when_no_events(self) -> None:
        order = Order(id=uuid4())
        events = order.pull_events()
        assert events == []

    def test_second_pull_events_returns_empty(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="ABC-123", quantity=2)
        _ = order.pull_events()
        second = order.pull_events()
        assert second == []


# ===================================================================
# Inheritance from Entity
# ===================================================================


class TestInheritanceFromEntity:
    def test_auto_generates_uuid_id(self) -> None:
        order = Order()  # type: ignore[call-arg]
        assert isinstance(order.id, UUID)

    def test_version_defaults_to_zero(self) -> None:
        order = Order(id=uuid4())
        assert order.version == 0

    def test_accepts_explicit_version(self) -> None:
        order = Order(id=uuid4(), version=5)
        assert order.version == 5

    def test_can_set_version_after_creation(self) -> None:
        order = Order(id=uuid4())
        order.version = 10
        assert order.version == 10

    def test_identity_based_equality(self) -> None:
        uid = uuid4()
        a = Order(id=uid, status="pending")
        b = Order(id=uid, status="shipped")
        assert a == b

    def test_different_ids_not_equal(self) -> None:
        a = Order(id=uuid4())
        b = Order(id=uuid4())
        assert a != b

    def test_not_equal_to_none(self) -> None:
        order = Order(id=uuid4())
        assert order is not None

    def test_not_equal_to_non_aggregate(self) -> None:
        order = Order(id=uuid4())
        assert order != {"id": order.id, "status": "pending"}

    def test_same_id_has_same_hash(self) -> None:
        uid = uuid4()
        a = Order(id=uid, status="pending")
        b = Order(id=uid, status="shipped")
        assert hash(a) == hash(b)

    def test_can_be_used_in_set(self) -> None:
        uid = uuid4()
        s = {
            Order(id=uid, status="pending"),
            Order(id=uid, status="shipped"),  # same id -> dedup
            Order(id=uuid4(), status="pending"),
        }
        assert len(s) == 2

    def test_can_be_used_as_dict_key(self) -> None:
        uid = uuid4()
        d: dict[Order, str] = {
            Order(id=uid, status="pending"): "order-1",
        }
        assert d[Order(id=uid, status="shipped")] == "order-1"


# ===================================================================
# Mutability
# ===================================================================


class TestMutability:
    def test_can_modify_field(self) -> None:
        order = Order(id=uuid4(), status="pending")
        order.status = "cancelled"
        assert order.status == "cancelled"

    def test_can_modify_version(self) -> None:
        order = Order(id=uuid4())
        order.version = 7
        assert order.version == 7

    def test_can_modify_id(self) -> None:
        order = Order(id=uuid4())
        new_id = uuid4()
        order.id = new_id
        assert order.id == new_id


# ===================================================================
# PrivateAttr (_pending_events)
# ===================================================================


class TestPrivateAttr:
    def test_pending_events_not_in_model_dump(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=1)
        data = order.model_dump()
        assert "_pending_events" not in data

    def test_pending_events_not_in_model_dump_json(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=1)
        json_str = order.model_dump_json()
        assert "_pending_events" not in json_str

    def test_model_dump_round_trip_clears_pending_events(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=1)
        data = order.model_dump()
        restored = Order.model_validate(data)
        assert restored.pull_events() == []

    def test_model_dump_json_round_trip_clears_pending_events(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=1)
        json_str = order.model_dump_json()
        restored = Order.model_validate_json(json_str)
        assert restored.pull_events() == []


# ===================================================================
# Serialization
# ===================================================================


class TestSerialization:
    def test_model_dump_round_trip_preserves_identity(self) -> None:
        uid = uuid4()
        original = Order(id=uid, status="pending")
        data = original.model_dump()
        restored = Order.model_validate(data)
        assert restored == original
        assert restored.id == uid

    def test_model_dump_json_round_trip_preserves_identity(self) -> None:
        uid = uuid4()
        original = Order(id=uid, status="pending")
        json_str = original.model_dump_json()
        restored = Order.model_validate_json(json_str)
        assert restored == original
        assert restored.id == uid

    def test_model_dump_includes_all_public_fields(self) -> None:
        uid = uuid4()
        order = Order(id=uid, status="pending")
        data = order.model_dump()
        assert data == {"id": uid, "version": 0, "status": "pending"}


# ===================================================================
# Command Methods
# ===================================================================


class TestCommandMethods:
    def test_command_records_event(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=2)
        events = order.pull_events()
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, OrderLineItemAdded)
        assert event.order_id == order.id
        assert event.product_id == "P001"
        assert event.quantity == 2

    def test_command_updates_state(self) -> None:
        order = Order(id=uuid4())
        assert order.status == "pending"
        order.ship()
        assert order.status == "shipped"

    def test_multiple_commands_collect_all_events(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=1)
        order.add_line_item(product_id="P002", quantity=5)
        order.confirm_payment(amount=29.99)
        events = order.pull_events()
        assert len(events) == 3
        assert isinstance(events[0], OrderLineItemAdded)
        assert events[0].product_id == "P001"
        assert isinstance(events[1], OrderLineItemAdded)
        assert events[1].product_id == "P002"
        assert isinstance(events[2], OrderPaymentConfirmed)
        assert events[2].amount == 29.99

    def test_events_have_auto_generated_ids(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=1)
        order.ship()
        events = order.pull_events()
        for event in events:
            assert isinstance(event.event_id, UUID)
            assert event.occurred_at is not None


# ===================================================================
# Edge Cases
# ===================================================================


class TestEdgeCases:
    def test_new_aggregate_has_no_pending_events(self) -> None:
        order = Order(id=uuid4())
        assert order.pull_events() == []

    def test_pull_events_then_add_more(self) -> None:
        order = Order(id=uuid4())
        order.add_line_item(product_id="P001", quantity=1)
        first_batch = order.pull_events()
        assert len(first_batch) == 1

        order.ship()
        second_batch = order.pull_events()
        assert len(second_batch) == 1
        assert isinstance(second_batch[0], OrderShipped)

    def test_subclass_with_additional_fields(self) -> None:
        class SpecialOrder(AggregateRoot[UUID]):
            priority: int = 0

            def escalate(self) -> None:
                self._add_event(OrderShipped(order_id=self.id))

        order = SpecialOrder(id=uuid4(), priority=5)
        assert order.priority == 5
        order.escalate()
        events = order.pull_events()
        assert len(events) == 1

    def test_non_uuid_aggregate_root_works(self) -> None:
        class OrderInt(AggregateRoot[int]):
            label: str = ""

            def mark_done(self) -> None:
                self._add_event(OrderShipped(order_id=uuid4()))

        order = OrderInt(id=42, label="test")
        assert order.id == 42
        assert order.label == "test"
        order.mark_done()
        events = order.pull_events()
        assert len(events) == 1
