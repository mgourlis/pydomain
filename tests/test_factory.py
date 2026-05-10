from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.ddd import AggregateRoot, Entity, Factory, ReconstitutionFactory

# ===================================================================
# Test domain objects (module-level, outside test classes)
#
# These must be module-level because Pydantic's BaseModel.__getattr__
# intercepts attribute access on model instances during validation.
# ===================================================================


class LineItem(Entity[UUID]):
    """A line item within an Order."""

    product_id: UUID
    quantity: int
    price: Decimal


class Order(AggregateRoot[UUID]):
    """An order consisting of one or more line items."""

    customer_id: UUID
    line_items: list[LineItem] = []

    def add_line_item(
        self, product_id: UUID, quantity: int, price: Decimal
    ) -> LineItem:
        """Factory Method: creates and appends a new LineItem.

        This is a lightweight alternative to a standalone Factory class
        when the creation logic is simple and lives naturally on the
        aggregate root.
        """
        if quantity <= 0:
            raise ValueError("Quantity must be positive")
        if price <= 0:
            raise ValueError("Price must be positive")

        item = LineItem(
            id=uuid4(), product_id=product_id, quantity=quantity, price=price
        )
        self.line_items.append(item)
        return item


# ---------------------------------------------------------------------------
# Standalone Factory implementing Factory[Order]
# ---------------------------------------------------------------------------


class OrderFactory:
    """Standalone Factory: creates Order aggregates.

    Demonstrates the Factory pattern with a simple injection placeholder
    (``pricing_service``).  A real factory might call an async pricing API
    or validate against a repository.
    """

    def __init__(self, pricing_service: Any = None) -> None:
        self._pricing_service = pricing_service

    def create(
        self,
        customer_id: UUID,
        *,
        items: list[tuple[UUID, int, Decimal]],
    ) -> Order:
        """Create an ``Order`` with the given line items.

        Raises ``ValueError`` when no items are provided — the invariant
        "an order must have at least one item" is enforced here.
        """
        if not items:
            raise ValueError("Order must have at least one line item")

        order = Order(id=uuid4(), customer_id=customer_id)
        for product_id, qty, price in items:
            order.add_line_item(product_id=product_id, quantity=qty, price=price)
        return order


# ---------------------------------------------------------------------------
# ReconstitutionFactory that rebuilds Orders from persisted data
# ---------------------------------------------------------------------------


class OrderReconstitutor:
    """Rebuilds an ``Order`` from previously persisted data.

    The key difference from ``OrderFactory.create``: **no new tracking ID
    is generated**.  The identity comes from the provided ``id`` parameter.
    This is used when replaying event-sourced aggregates or rehydrating
    from a repository.
    """

    def reconstitute(
        self,
        id: UUID,
        customer_id: UUID,
        line_items: list[LineItem],
    ) -> Order:
        return Order(id=id, customer_id=customer_id, line_items=line_items)


# ---------------------------------------------------------------------------
# Async Factory for testing async dependency injection
# ---------------------------------------------------------------------------


class AsyncPricingService:
    """Simulates an async pricing validation service."""

    async def validate_total(self, items: list[LineItem]) -> Decimal:
        return sum((item.price * item.quantity for item in items), Decimal(0))


class AsyncOrderFactory:
    """A Factory whose ``create`` uses async dependencies."""

    def __init__(self, pricing: AsyncPricingService) -> None:
        self._pricing = pricing

    async def create(
        self,
        customer_id: UUID,
        *,
        items: list[tuple[UUID, int, Decimal]],
    ) -> Order:
        if not items:
            raise ValueError("Order must have at least one line item")

        order = Order(id=uuid4(), customer_id=customer_id)
        for product_id, qty, price in items:
            order.add_line_item(product_id=product_id, quantity=qty, price=price)
        return order


# ===================================================================
# Factory protocol conformance
# ===================================================================


class TestFactoryProtocolConformance:
    def test_standalone_factory_conforms_to_protocol(self) -> None:
        """OrderFactory structurally conforms to Factory[Order]."""
        factory: Factory[Order] = OrderFactory()
        assert isinstance(factory, Factory)

    def test_reconstitutor_conforms_to_protocol(self) -> None:
        """OrderReconstitutor structurally conforms to ReconstitutionFactory[Order]."""
        reconstitutor: ReconstitutionFactory[Order] = OrderReconstitutor()
        assert isinstance(reconstitutor, ReconstitutionFactory)

    def test_non_factory_does_not_conform(self) -> None:
        """A plain object does not conform to Factory."""

        class NotAFactory:
            def not_create(self) -> Order:  # wrong method name
                raise NotImplementedError

        obj: Any = NotAFactory()
        assert not isinstance(obj, Factory)

    def test_wrong_return_type_does_not_matter_at_runtime(self) -> None:
        """runtime_checkable only checks method presence, not signature."""

        class WrongReturn:
            def create(self, *args: Any, **kwargs: Any) -> int:
                return 42

        assert isinstance(WrongReturn(), Factory)  # noqa: E711


# ===================================================================
# Standalone Factory
# ===================================================================


class TestStandaloneFactory:
    def test_creates_order_with_items(self) -> None:
        customer_id = uuid4()
        product_id = uuid4()
        factory = OrderFactory()

        order = factory.create(
            customer_id=customer_id,
            items=[(product_id, 2, Decimal("19.99"))],
        )

        assert isinstance(order, Order)
        assert order.customer_id == customer_id
        assert len(order.line_items) == 1
        assert order.line_items[0].product_id == product_id
        assert order.line_items[0].quantity == 2
        assert order.line_items[0].price == Decimal("19.99")

    def test_creates_order_with_multiple_items(self) -> None:
        customer_id = uuid4()
        p1, p2 = uuid4(), uuid4()
        factory = OrderFactory()

        order = factory.create(
            customer_id=customer_id,
            items=[
                (p1, 1, Decimal("9.99")),
                (p2, 3, Decimal("14.50")),
            ],
        )

        assert len(order.line_items) == 2
        assert order.line_items[0].product_id == p1
        assert order.line_items[1].product_id == p2

    def test_auto_generates_order_id_when_not_provided(self) -> None:
        """Factory.create does not receive an ``id`` for new aggregates."""
        factory = OrderFactory()
        order = factory.create(
            customer_id=uuid4(),
            items=[(uuid4(), 1, Decimal("5.00"))],
        )
        assert isinstance(order.id, UUID)

    def test_auto_generates_line_item_ids(self) -> None:
        """Factory Method creates LineItems with auto-generated IDs."""
        factory = OrderFactory()
        order = factory.create(
            customer_id=uuid4(),
            items=[(uuid4(), 1, Decimal("5.00"))],
        )
        line_item = order.line_items[0]
        assert isinstance(line_item.id, UUID)
        assert line_item.id is not None

    def test_each_line_item_has_unique_id(self) -> None:
        factory = OrderFactory()
        order = factory.create(
            customer_id=uuid4(),
            items=[
                (uuid4(), 1, Decimal("5.00")),
                (uuid4(), 2, Decimal("3.00")),
            ],
        )
        assert order.line_items[0].id != order.line_items[1].id

    def test_returns_same_order_with_pull_events_empty(self) -> None:
        """A freshly created aggregate has no pending domain events (creation
        alone does not record a domain event)."""
        factory = OrderFactory()
        order = factory.create(
            customer_id=uuid4(),
            items=[(uuid4(), 1, Decimal("5.00"))],
        )
        assert order.pull_events() == []


# ===================================================================
# Factory Method on Aggregate Root
# ===================================================================


class TestFactoryMethod:
    def test_add_line_item_appends_item(self) -> None:
        order = Order(id=uuid4(), customer_id=uuid4())
        product_id = uuid4()

        order.add_line_item(product_id=product_id, quantity=2, price=Decimal("9.99"))

        assert len(order.line_items) == 1
        item = order.line_items[0]
        assert isinstance(item, LineItem)
        assert item.product_id == product_id
        assert item.quantity == 2
        assert item.price == Decimal("9.99")

    def test_add_line_item_auto_generates_line_item_id(self) -> None:
        order = Order(id=uuid4(), customer_id=uuid4())
        order.add_line_item(product_id=uuid4(), quantity=1, price=Decimal("5.00"))
        item = order.line_items[0]
        assert isinstance(item.id, UUID)

    def test_multiple_items_accumulate(self) -> None:
        order = Order(id=uuid4(), customer_id=uuid4())
        order.add_line_item(product_id=uuid4(), quantity=1, price=Decimal("5.00"))
        order.add_line_item(product_id=uuid4(), quantity=2, price=Decimal("3.50"))
        assert len(order.line_items) == 2


# ===================================================================
# Invariants enforced during creation
# ===================================================================


class TestCreationInvariants:
    def test_cannot_create_order_with_no_items(self) -> None:
        factory = OrderFactory()
        with pytest.raises(ValueError, match="at least one line item"):
            factory.create(
                customer_id=uuid4(),
                items=[],
            )

    def test_cannot_add_line_item_with_zero_quantity(self) -> None:
        order = Order(id=uuid4(), customer_id=uuid4())
        with pytest.raises(ValueError, match="Quantity must be positive"):
            order.add_line_item(product_id=uuid4(), quantity=0, price=Decimal("5.00"))

    def test_cannot_add_line_item_with_negative_quantity(self) -> None:
        order = Order(id=uuid4(), customer_id=uuid4())
        with pytest.raises(ValueError, match="Quantity must be positive"):
            order.add_line_item(product_id=uuid4(), quantity=-1, price=Decimal("5.00"))

    def test_cannot_add_line_item_with_zero_price(self) -> None:
        order = Order(id=uuid4(), customer_id=uuid4())
        with pytest.raises(ValueError, match="Price must be positive"):
            order.add_line_item(product_id=uuid4(), quantity=1, price=Decimal("0"))

    def test_cannot_add_line_item_with_negative_price(self) -> None:
        order = Order(id=uuid4(), customer_id=uuid4())
        with pytest.raises(ValueError, match="Price must be positive"):
            order.add_line_item(product_id=uuid4(), quantity=1, price=Decimal("-1.00"))


# ===================================================================
# Reconstitution — no new tracking IDs
# ===================================================================


class TestReconstitution:
    def test_reconstitute_preserves_order_id(self) -> None:
        original_id = uuid4()
        customer_id = uuid4()
        product_id = uuid4()
        reconstitutor = OrderReconstitutor()

        order = reconstitutor.reconstitute(
            id=original_id,
            customer_id=customer_id,
            line_items=[
                LineItem(
                    id=uuid4(),
                    product_id=product_id,
                    quantity=2,
                    price=Decimal("19.99"),
                )
            ],
        )

        assert order.id == original_id

    def test_reconstitute_preserves_line_item_ids(self) -> None:
        original_id = uuid4()
        line_item_id = uuid4()
        customer_id = uuid4()
        reconstitutor = OrderReconstitutor()

        order = reconstitutor.reconstitute(
            id=original_id,
            customer_id=customer_id,
            line_items=[
                LineItem(
                    id=line_item_id,
                    product_id=uuid4(),
                    quantity=2,
                    price=Decimal("19.99"),
                )
            ],
        )

        assert order.line_items[0].id == line_item_id

    def test_reconstitute_preserves_all_fields(self) -> None:
        original_id = uuid4()
        customer_id = uuid4()
        line_item_id = uuid4()
        product_id = uuid4()
        reconstitutor = OrderReconstitutor()

        order = reconstitutor.reconstitute(
            id=original_id,
            customer_id=customer_id,
            line_items=[
                LineItem(
                    id=line_item_id,
                    product_id=product_id,
                    quantity=2,
                    price=Decimal("19.99"),
                )
            ],
        )

        assert order.id == original_id
        assert order.customer_id == customer_id
        assert order.line_items[0].id == line_item_id
        assert order.line_items[0].product_id == product_id
        assert order.line_items[0].quantity == 2
        assert order.line_items[0].price == Decimal("19.99")

    def test_reconstitute_with_multiple_line_items(self) -> None:
        id1, id2 = uuid4(), uuid4()
        order = OrderReconstitutor().reconstitute(
            id=uuid4(),
            customer_id=uuid4(),
            line_items=[
                LineItem(id=id1, product_id=uuid4(), quantity=1, price=Decimal("5.00")),
                LineItem(id=id2, product_id=uuid4(), quantity=3, price=Decimal("2.50")),
            ],
        )
        assert len(order.line_items) == 2
        assert order.line_items[0].id == id1
        assert order.line_items[1].id == id2

    def test_reconstitute_new_order_has_no_pending_events(self) -> None:
        order = OrderReconstitutor().reconstitute(
            id=uuid4(),
            customer_id=uuid4(),
            line_items=[],
        )
        assert order.pull_events() == []


# ===================================================================
# Factory with async dependencies
# ===================================================================


class TestAsyncFactory:
    @pytest.mark.anyio
    async def test_async_factory_creates_order(self) -> None:
        pricing = AsyncPricingService()
        factory = AsyncOrderFactory(pricing)

        order = await factory.create(
            customer_id=uuid4(),
            items=[(uuid4(), 2, Decimal("19.99"))],
        )

        assert isinstance(order, Order)
        assert len(order.line_items) == 1

    @pytest.mark.anyio
    async def test_async_factory_enforces_invariants(self) -> None:
        pricing = AsyncPricingService()
        factory = AsyncOrderFactory(pricing)

        with pytest.raises(ValueError, match="at least one line item"):
            await factory.create(
                customer_id=uuid4(),
                items=[],
            )

    @pytest.mark.anyio
    async def test_async_factory_auto_generates_ids(self) -> None:
        pricing = AsyncPricingService()
        factory = AsyncOrderFactory(pricing)

        order = await factory.create(
            customer_id=uuid4(),
            items=[(uuid4(), 1, Decimal("9.99"))],
        )

        assert isinstance(order.id, UUID)
        assert isinstance(order.line_items[0].id, UUID)


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    def test_standalone_factory_accepts_no_pricing_service(self) -> None:
        """The dependency injection parameter is optional."""
        factory = OrderFactory()  # no pricing_service argument
        assert factory._pricing_service is None

    def test_standalone_factory_with_pricing_service(self) -> None:
        """Dependency injection works via the constructor."""
        factory = OrderFactory(pricing_service="mock-pricing")
        assert factory._pricing_service == "mock-pricing"

    def test_line_item_default_quantity_works(self) -> None:
        """Verify the LineItem model works with all fields provided."""
        item = LineItem(
            id=uuid4(),
            product_id=uuid4(),
            quantity=1,
            price=Decimal("10.00"),
        )
        assert item.quantity == 1
        assert item.price == Decimal("10.00")

    def test_reconstitute_with_zero_line_items(self) -> None:
        """Reconstituting an order with no line items is valid (edge case)."""
        order = OrderReconstitutor().reconstitute(
            id=uuid4(),
            customer_id=uuid4(),
            line_items=[],
        )
        assert order.line_items == []

    def test_factory_method_returns_line_item(self) -> None:
        """Verify ``add_line_item`` returns the created ``LineItem``."""
        order = Order(id=uuid4(), customer_id=uuid4())
        product_id = uuid4()
        item = order.add_line_item(
            product_id=product_id, quantity=1, price=Decimal("5.00")
        )
        assert isinstance(item, LineItem)
        assert item.product_id == product_id
