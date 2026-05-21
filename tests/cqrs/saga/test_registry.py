"""Tests for SagaRegistry — event → saga mapping."""

from __future__ import annotations

import pytest

from pydomain.cqrs.saga.exceptions import SagaConfigurationError
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState
from pydomain.ddd.domain_event import DomainEvent

# ── Test domain events ──────────────────────────────────────────────────


class OrderCreated(DomainEvent):
    order_id: str


class OrderShipped(DomainEvent):
    order_id: str


class PaymentReceived(DomainEvent):
    payment_id: str


# ── Test sagas ──────────────────────────────────────────────────────────


class OrderSaga(Saga[SagaState]):
    listens_to = [OrderCreated, OrderShipped]


class PaymentSaga(Saga[SagaState]):
    listens_to = [PaymentReceived]


class MultiEventSaga(Saga[SagaState]):
    """Listens to the same event as OrderSaga to test multi-registration."""

    listens_to = [OrderCreated]


class NoListenSaga(Saga[SagaState]):
    """Saga with no listens_to — tests name-only registration."""

    listens_to = []


# ── Tests ───────────────────────────────────────────────────────────────


class TestRegisterSaga:
    """register_saga() — bulk registration via listens_to."""

    def test_register_saga_for_events(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(OrderSaga)
        sagas = registry.get_sagas_for_event(OrderCreated)
        assert OrderSaga in sagas

    def test_register_saga_for_multiple_events(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(OrderSaga)
        assert OrderSaga in registry.get_sagas_for_event(OrderCreated)
        assert OrderSaga in registry.get_sagas_for_event(OrderShipped)

    def test_register_saga_still_registered_by_name(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(OrderSaga)
        assert registry.get_saga_type("OrderSaga") is OrderSaga

    def test_register_saga_with_empty_listens_to(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(NoListenSaga)
        # No events registered, but still available by name
        assert registry.get_saga_type("NoListenSaga") is NoListenSaga
        assert registry.registered_event_types == set()


class TestRegister:
    """register() — per-event registration."""

    def test_register_event_type(self) -> None:
        registry = SagaRegistry()
        registry.register(OrderCreated, OrderSaga)
        assert OrderSaga in registry.get_sagas_for_event(OrderCreated)

    def test_register_does_not_duplicate(self) -> None:
        registry = SagaRegistry()
        registry.register(OrderCreated, OrderSaga)
        registry.register(OrderCreated, OrderSaga)
        assert len(registry.get_sagas_for_event(OrderCreated)) == 1

    def test_multiple_sagas_for_same_event(self) -> None:
        registry = SagaRegistry()
        registry.register(OrderCreated, OrderSaga)
        registry.register(OrderCreated, MultiEventSaga)
        sagas = registry.get_sagas_for_event(OrderCreated)
        assert OrderSaga in sagas
        assert MultiEventSaga in sagas
        assert len(sagas) == 2


class TestQueries:
    """Registry query methods."""

    def test_get_sagas_for_unregistered_event(self) -> None:
        registry = SagaRegistry()
        assert registry.get_sagas_for_event(OrderCreated) == []

    def test_get_saga_type_not_found(self) -> None:
        registry = SagaRegistry()
        assert registry.get_saga_type("NonExistent") is None

    def test_registered_event_types(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(OrderSaga)
        registry.register_saga(PaymentSaga)
        event_types = registry.registered_event_types
        assert OrderCreated in event_types
        assert OrderShipped in event_types
        assert PaymentReceived in event_types

    def test_registered_event_types_empty(self) -> None:
        registry = SagaRegistry()
        assert registry.registered_event_types == set()


class TestClear:
    """clear() removes all registrations."""

    def test_clear(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(OrderSaga)
        registry.register_saga(PaymentSaga)
        registry.clear()
        assert registry.get_sagas_for_event(OrderCreated) == []
        assert registry.get_saga_type("OrderSaga") is None
        assert registry.registered_event_types == set()


class TestRegisterSagaStrict:
    """register_saga(strict=True) raises on empty listens_to."""

    def test_strict_raises_on_empty_listens_to(self) -> None:
        registry = SagaRegistry()
        with pytest.raises(SagaConfigurationError, match="NoListenSaga"):
            registry.register_saga(NoListenSaga, strict=True)

    def test_strict_does_not_raise_with_events(self) -> None:
        registry = SagaRegistry()
        # Should not raise — OrderSaga has events in listens_to
        registry.register_saga(OrderSaga, strict=True)
        assert OrderSaga in registry.get_sagas_for_event(OrderCreated)

    def test_non_strict_logs_warning_on_empty_listens_to(self) -> None:
        registry = SagaRegistry()
        # Default strict=False — should log warning, not raise
        registry.register_saga(NoListenSaga)
        assert registry.get_saga_type("NoListenSaga") is NoListenSaga
