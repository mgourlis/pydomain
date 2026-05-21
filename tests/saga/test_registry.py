"""Tests for SagaRegistry — event_map, type_map, registration, queries."""

from __future__ import annotations

from pydomain.cqrs.saga.registry import SagaRegistry

from .conftest import (
    AuditSaga,
    FiveStepSaga,
    ItemsReserved,
    NoListenSaga,
    OrderCreated,
    PaymentProcessed,
    TwoStepSaga,
)

# ═══════════════════════════════════════════════════════════════════════
# register_saga()
# ═══════════════════════════════════════════════════════════════════════


class TestRegisterSaga:
    """register_saga() adds saga to both event_map and type_map."""

    def test_register_single_saga(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        assert registry.get_saga_type("TwoStepSaga") is TwoStepSaga

    def test_register_multiple_sagas(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        assert registry.get_saga_type("TwoStepSaga") is TwoStepSaga
        assert registry.get_saga_type("AuditSaga") is AuditSaga

    def test_register_populates_event_map(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        sagas = registry.get_sagas_for_event(OrderCreated)
        assert TwoStepSaga in sagas

    def test_register_no_listen_saga(self) -> None:
        """Saga with empty listens_to registers in type_map only."""
        registry = SagaRegistry()
        registry.register_saga(NoListenSaga)
        assert registry.get_saga_type("NoListenSaga") is NoListenSaga
        assert registry.registered_event_types == set()

    def test_register_same_saga_twice_is_idempotent(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(TwoStepSaga)
        sagas = registry.get_sagas_for_event(OrderCreated)
        assert sagas.count(TwoStepSaga) == 1


# ═══════════════════════════════════════════════════════════════════════
# register() — event-level registration
# ═══════════════════════════════════════════════════════════════════════


class TestRegister:
    """register() adds an event → saga mapping."""

    def test_register_event_to_saga(self) -> None:
        registry = SagaRegistry()
        registry.register(OrderCreated, TwoStepSaga)
        sagas = registry.get_sagas_for_event(OrderCreated)
        assert TwoStepSaga in sagas

    def test_register_multiple_sagas_for_same_event(self) -> None:
        registry = SagaRegistry()
        registry.register(OrderCreated, TwoStepSaga)
        registry.register(OrderCreated, AuditSaga)
        sagas = registry.get_sagas_for_event(OrderCreated)
        assert len(sagas) == 2
        assert TwoStepSaga in sagas
        assert AuditSaga in sagas

    def test_register_different_events_to_same_saga(self) -> None:
        registry = SagaRegistry()
        registry.register(OrderCreated, TwoStepSaga)
        registry.register(ItemsReserved, TwoStepSaga)
        assert TwoStepSaga in registry.get_sagas_for_event(OrderCreated)
        assert TwoStepSaga in registry.get_sagas_for_event(ItemsReserved)


# ═══════════════════════════════════════════════════════════════════════
# register_type() — type-level registration
# ═══════════════════════════════════════════════════════════════════════


class TestRegisterType:
    """register_type() maps saga type name to saga class."""

    def test_register_type(self) -> None:
        registry = SagaRegistry()
        registry.register_type(TwoStepSaga)
        assert registry.get_saga_type("TwoStepSaga") is TwoStepSaga

    def test_register_type_does_not_add_to_event_map(self) -> None:
        registry = SagaRegistry()
        registry.register_type(TwoStepSaga)
        assert registry.registered_event_types == set()


# ═══════════════════════════════════════════════════════════════════════
# Query Methods
# ═══════════════════════════════════════════════════════════════════════


class TestRegistryQueries:
    """get_sagas_for_event, get_saga_type, registered_event_types."""

    def test_get_sagas_for_event_empty(self) -> None:
        registry = SagaRegistry()
        assert registry.get_sagas_for_event(OrderCreated) == []

    def test_get_sagas_for_event_returns_correct_sagas(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        sagas = registry.get_sagas_for_event(OrderCreated)
        assert len(sagas) == 2

    def test_get_saga_type_returns_none_for_unknown(self) -> None:
        registry = SagaRegistry()
        assert registry.get_saga_type("NonExistentSaga") is None

    def test_registered_event_types(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        events = registry.registered_event_types
        assert OrderCreated in events
        assert ItemsReserved in events

    def test_registered_event_types_deduplicates(self) -> None:
        """Multiple sagas listening to same event → event appears once."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        events = registry.registered_event_types
        # Both listen to OrderCreated, but it appears once
        order_created_count = sum(1 for e in events if e == OrderCreated)
        assert order_created_count == 1


# ═══════════════════════════════════════════════════════════════════════
# clear()
# ═══════════════════════════════════════════════════════════════════════


class TestRegistryClear:
    """clear() removes all registrations."""

    def test_clear_empties_registry(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        registry.clear()
        assert registry.get_sagas_for_event(OrderCreated) == []
        assert registry.get_saga_type("TwoStepSaga") is None
        assert registry.registered_event_types == set()

    def test_clear_on_empty_registry(self) -> None:
        registry = SagaRegistry()
        registry.clear()  # should not raise
        assert registry.registered_event_types == set()


# ═══════════════════════════════════════════════════════════════════════
# Multi-Saga Scenarios
# ═══════════════════════════════════════════════════════════════════════


class TestRegistryMultiSaga:
    """Multiple sagas for same event, independent state."""

    def test_two_sagas_same_event(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        sagas = registry.get_sagas_for_event(OrderCreated)
        assert len(sagas) == 2

    def test_sagas_independent_event_lists(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(FiveStepSaga)
        assert TwoStepSaga in registry.get_sagas_for_event(ItemsReserved)
        assert FiveStepSaga in registry.get_sagas_for_event(PaymentProcessed)
        # TwoStepSaga does NOT listen to PaymentProcessed
        assert TwoStepSaga not in registry.get_sagas_for_event(PaymentProcessed)
