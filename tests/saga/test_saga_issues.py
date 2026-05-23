"""TDD tests for saga issues — each test targets a confirmed issue.

RED phase: all tests that target real bugs or design flaws should FAIL
against the current codebase. Tests targeting already-fixed issues
(whitelisted as skipped) should PASS.

Issue key:
    #2/#3 — SagaManager.handle breaks loop / re-raise after error
    #4    — Forward dispatch clears ALL pending commands
    #5    — No retry_count guard during forward dispatch
    #6    — Memory bounds are instance fields (design: ClassVar)
    #7    — prune_history doesn't bump version
    #8    — Event not marked processed on terminal failure
    #9    — on() accepts conflicting complete+suspend
    #10   — causation_id never updated after creation
    #11   — Suspended sagas resumed by any event
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga.exceptions import SagaConfigurationError
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from .conftest import (
    ApprovalGranted,
    AuditSaga,
    CancelReservation,
    ConfirmOrder,
    ItemsReserved,
    OrderCreated,
    OrderFailed,
    ReserveItems,
    SendNotification,
    TwoStepSaga,
    _noop_command_bus,
)

# ═══════════════════════════════════════════════════════════════════════
# Helper saga definitions for issue tests
# ═══════════════════════════════════════════════════════════════════════


class _FailOnHandleSaga(Saga[SagaState]):
    """Saga that raises on every event — for isolation tests."""

    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, handler=self._fail)

    async def _fail(self, event: DomainEvent) -> None:
        raise RuntimeError(f"Saga {_FailOnHandleSaga.__name__} intentional failure")


class _FailOnHandleSagaAlt(Saga[SagaState]):
    """Second failing saga — distinct from _FailOnHandleSaga."""

    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, handler=self._fail)

    async def _fail(self, event: DomainEvent) -> None:
        raise RuntimeError(f"Saga {_FailOnHandleSagaAlt.__name__} intentional failure")


class _DispatchFailSaga(Saga[SagaState]):
    """Saga that dispatches a command which will fail at the bus level."""

    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
        )


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_repo() -> FakeSagaRepository:
    return FakeSagaRepository()


@pytest.fixture
def command_bus() -> CommandBus:
    return _noop_command_bus()


def _failing_command_bus(
    fail_on: type[Command[Any]] = ReserveItems,
) -> CommandBus:
    """Command bus that raises on a specific command type."""
    bus = CommandBus()

    async def fail(cmd: Any, uow: Any = None) -> EmptyCommandResult:
        raise RuntimeError(f"Command {type(cmd).__name__} intentionally failed")

    async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
        return EmptyCommandResult()

    for cmd_type in (
        ReserveItems,
        ConfirmOrder,
        CancelReservation,
        SendNotification,
    ):
        handler = fail if cmd_type is fail_on else noop
        bus.register(cmd_type, handler, uow_factory=lambda: FakeUnitOfWork())

    return bus


# ═══════════════════════════════════════════════════════════════════════
# Issue #2 / #3 — Saga isolation in handle() loop
# ═══════════════════════════════════════════════════════════════════════


class TestSagaIsolation:
    """One saga's failure must not prevent other sagas from processing.

    Issue #2: handle() breaks the loop.
    Issue #3: re-raise propagates to handle().
    """

    @pytest.mark.anyio
    async def test_failing_saga_does_not_prevent_other_sagas_from_processing(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """If SagaA fails, SagaB should still process the event."""
        registry = SagaRegistry()
        registry.register_saga(_FailOnHandleSaga)
        registry.register_saga(AuditSaga)  # uses SendNotification, should succeed

        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=command_bus,
        )

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)

        # Should NOT raise — the failing saga is handled internally
        await mgr.handle(event)

        # AuditSaga should have been created and completed
        audit_state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert audit_state is not None, "AuditSaga should have been created"
        assert audit_state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_both_sagas_processed_even_when_first_fails(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Both sagas get the event regardless of order."""
        registry = SagaRegistry()
        registry.register_saga(AuditSaga)
        registry.register_saga(_FailOnHandleSaga)  # registered second

        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=command_bus,
        )

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)

        await mgr.handle(event)

        # Both should exist — AuditSaga completed, FailOnHandleSaga failed
        audit_state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        fail_state = await saga_repo.find_by_correlation_id(cid, "_FailOnHandleSaga")

        assert audit_state is not None
        assert audit_state.status == SagaStatus.COMPLETED
        assert fail_state is not None
        assert fail_state.status == SagaStatus.FAILED

    @pytest.mark.anyio
    async def test_two_failing_sagas_both_get_processed(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Both failing sagas should be in FAILED state."""
        registry = SagaRegistry()
        registry.register_saga(_FailOnHandleSaga)
        registry.register_saga(_FailOnHandleSagaAlt)

        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=command_bus,
        )

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)

        await mgr.handle(event)

        state_a = await saga_repo.find_by_correlation_id(cid, "_FailOnHandleSaga")
        state_b = await saga_repo.find_by_correlation_id(cid, "_FailOnHandleSagaAlt")

        assert state_a is not None
        assert state_a.status == SagaStatus.FAILED
        assert state_b is not None
        assert state_b.status == SagaStatus.FAILED

    @pytest.mark.anyio
    async def test_handle_does_not_raise_on_saga_failure(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """handle() should swallow saga exceptions, not propagate them."""
        registry = SagaRegistry()
        registry.register_saga(_FailOnHandleSaga)

        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=command_bus,
        )

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)

        # This should NOT raise
        await mgr.handle(event)


# ═══════════════════════════════════════════════════════════════════════
# Issue #4 — Forward dispatch clears ALL pending commands
# ═══════════════════════════════════════════════════════════════════════


class TestPendingCommandsIsolation:
    """clear() should only remove the dispatched batch, not older entries."""

    @pytest.mark.anyio
    async def test_successful_dispatch_preserves_older_undispatched_commands(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """If a saga had stale undispatched commands, they must survive
        a subsequent successful dispatch round."""

        # Manually set up a state with one stale undispatched command
        stale_state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            status=SagaStatus.RUNNING,
        )
        # Simulate a previously undispatched command from a failed dispatch
        stale_state.pending_commands = [
            {
                "command_type": "ReserveItems",
                "module_name": "tests.saga.conftest",
                "data": {
                    "order_id": "STALE-1",
                    "item_count": 1,
                    "command_id": str(uuid4()),
                    "correlation_id": None,
                    "causation_id": None,
                },
                "dispatched": False,
            }
        ]
        stale_state.current_step = "stale_step"
        await saga_repo.save(stale_state)

        # Now set up a manager that will successfully dispatch new commands
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)

        bus = CommandBus()

        dispatched: list[Command[Any]] = []

        async def capture(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())
        bus.register(ConfirmOrder, capture, uow_factory=lambda: FakeUnitOfWork())

        mgr = SagaManager(repository=saga_repo, registry=registry, command_bus=bus)

        # Send an ItemsReserved event to trigger the confirm step
        # This will find the existing state and dispatch a new ConfirmOrder
        await mgr.handle(
            ItemsReserved(
                order_id="ORD-1",
                correlation_id=stale_state.correlation_id,
            )
        )

        state = await saga_repo.find_by_correlation_id(
            stale_state.correlation_id, "TwoStepSaga"
        )
        assert state is not None

        # The stale undispatched command should still be there
        stale_cmds = [
            c for c in state.pending_commands if not c.get("dispatched", False)
        ]
        assert len(stale_cmds) >= 1, (
            "Stale undispatched command should survive a new successful dispatch"
        )


# ═══════════════════════════════════════════════════════════════════════
# Issue #5 — No retry_count guard during forward dispatch
# ═══════════════════════════════════════════════════════════════════════


class TestRetryCountGuard:
    """Exhausted retry_count should prevent further dispatch attempts."""

    @pytest.mark.anyio
    async def test_exhausted_retries_prevents_dispatch(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """A saga with retry_count >= max_retries should not attempt
        command dispatch."""

        # Create a state that has exhausted retries
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
            retry_count=3,
            max_retries=3,
        )
        await saga_repo.save(state)

        # Set up a bus that would fail if dispatch is attempted
        dispatch_attempted = False
        bus = CommandBus()

        async def should_not_be_called(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            nonlocal dispatch_attempted
            dispatch_attempted = True
            return EmptyCommandResult()

        bus.register(
            ReserveItems,
            should_not_be_called,
            uow_factory=lambda: FakeUnitOfWork(),
        )

        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = SagaManager(repository=saga_repo, registry=registry, command_bus=bus)

        # Send an event that would normally trigger dispatch
        await mgr.handle(
            OrderCreated(
                order_id="ORD-RETRY",
                correlation_id=state.correlation_id,
            )
        )

        assert not dispatch_attempted, (
            "Dispatch should not be attempted when retry_count >= max_retries"
        )

    @pytest.mark.anyio
    async def test_exhausted_retries_transitions_to_failed(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """A saga with exhausted retries should transition to FAILED."""

        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
            retry_count=3,
            max_retries=3,
        )
        await saga_repo.save(state)

        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=_noop_command_bus(),
        )

        await mgr.handle(
            OrderCreated(
                order_id="ORD-RETRY",
                correlation_id=state.correlation_id,
            )
        )

        state = await saga_repo.find_by_correlation_id(
            state.correlation_id, "TwoStepSaga"
        )
        assert state is not None
        assert state.status == SagaStatus.FAILED, (
            "Saga should be FAILED when retries are exhausted"
        )


# ═══════════════════════════════════════════════════════════════════════
# Issue #6 — Memory bounds are instance fields (design concern)
# ═══════════════════════════════════════════════════════════════════════


class TestMemoryBoundsConfig:
    """max_processed_events / max_step_history should behave as config,
    not serialised state."""

    def test_memory_bounds_not_in_serialised_output(self) -> None:
        """When serialised, config fields should not appear in output."""
        state = SagaState(
            saga_type="TestSaga",
        )
        # Set class-level config
        SagaState.max_processed_events = 100  # type: ignore[attr-defined]
        SagaState.max_step_history = 50  # type: ignore[attr-defined]
        try:
            dumped = state.model_dump()

            assert "max_processed_events" not in dumped, (
                "max_processed_events should not be serialised with state data"
            )
            assert "max_step_history" not in dumped, (
                "max_step_history should not be serialised with state data"
            )
        finally:
            SagaState.max_processed_events = 0  # type: ignore[attr-defined]
            SagaState.max_step_history = 0  # type: ignore[attr-defined]

    def test_memory_bounds_shared_across_instances(self) -> None:
        """Config should be class-level, shared across all instances."""
        # Override at the class level
        original = SagaState.max_processed_events
        SagaState.max_processed_events = 10  # type: ignore[attr-defined]
        try:
            s1 = SagaState(saga_type="A")
            s2 = SagaState(saga_type="B")

            assert s1.max_processed_events == 10
            assert s2.max_processed_events == 10
        finally:
            SagaState.max_processed_events = original  # type: ignore[attr-defined]

    def test_deserialisation_does_not_override_class_config(self) -> None:
        """Persistence should not silently override class-level config."""
        state = SagaState(saga_type="TestSaga")
        assert state.max_processed_events == 0


# ═══════════════════════════════════════════════════════════════════════
# Issue #7 — prune_history doesn't bump version / updated_at
# ═══════════════════════════════════════════════════════════════════════


class TestPruneHistoryVersionBump:
    """prune_history() must bump version and updated_at."""

    def test_prune_history_bumps_version(self) -> None:
        state = SagaState(saga_type="TestSaga")
        for i in range(10):
            state.record_step(f"step{i}", f"Event{i}")

        version_before = state.version
        state.prune_history(keep_last_n_steps=3)
        version_after = state.version

        assert version_after > version_before, (
            f"prune_history must bump version: was {version_before}, "
            f"became {version_after}"
        )

    def test_prune_history_updates_updated_at(self) -> None:
        state = SagaState(saga_type="TestSaga")
        for i in range(5):
            state.mark_event_processed(uuid4())

        updated_at_before = state.updated_at
        state.prune_history(keep_last_n_events=2)

        assert state.updated_at > updated_at_before, (
            "prune_history must update updated_at"
        )

    def test_prune_history_touch_increments_version_by_one(self) -> None:
        """Exactly +1 version increment, matching touch() behaviour."""
        state = SagaState(saga_type="TestSaga")
        state.record_step("step0", "E0")
        state.record_step("step1", "E1")

        version_after_steps = state.version
        state.prune_history(keep_last_n_steps=1)

        assert state.version == version_after_steps + 1

    def test_prune_history_noop_still_bumps(self) -> None:
        """Even if nothing is pruned, touch() should still be called
        to record the operation."""

        state = SagaState(saga_type="TestSaga")
        state.record_step("step0", "E0")

        version_before = state.version
        state.prune_history()  # None, None — no actual pruning

        assert state.version > version_before, (
            "prune_history should call touch() even when no data is pruned"
        )


# ═══════════════════════════════════════════════════════════════════════
# Issue #8 — Event not marked processed on terminal failure
# ═══════════════════════════════════════════════════════════════════════


class TestIdempotencyOnFailure:
    """When a handler fails and saga goes terminal, the event should be
    marked as processed to prevent wasteful re-delivery."""

    @pytest.mark.anyio
    async def test_failing_event_is_marked_processed(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """The event that caused terminal failure should be in
        processed_event_ids."""

        registry = SagaRegistry()
        registry.register_saga(_FailOnHandleSaga)

        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=command_bus,
        )

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "_FailOnHandleSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert state.is_event_processed(event.event_id), (
            "Event that caused failure should be marked as processed"
        )

    @pytest.mark.anyio
    async def test_compensated_saga_marks_event_processed(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Even when compensation runs, the triggering event should be
        marked processed."""

        class CompensateOnFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"), "Cancel reservation"
                )

            async def _step2_fail(self, event: DomainEvent) -> None:
                raise RuntimeError("Step 2 intentional failure")

        registry = SagaRegistry()
        registry.register_saga(CompensateOnFailSaga)
        mgr = SagaManager(
            repository=saga_repo, registry=registry, command_bus=command_bus
        )

        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event1)

        fail_event = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(fail_event)

        state = await saga_repo.find_by_correlation_id(cid, "CompensateOnFailSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert state.is_event_processed(fail_event.event_id), (
            "Event that triggered compensation should be marked processed"
        )


# ═══════════════════════════════════════════════════════════════════════
# Issue #9 — on() accepts conflicting complete and suspend flags
# ═══════════════════════════════════════════════════════════════════════


class TestConflictingCompleteSuspend:
    """on(complete=True, suspend=True) should raise, not silently pick one."""

    @pytest.mark.anyio
    async def test_complete_and_suspend_raises_configuration_error(
        self,
    ) -> None:
        state = SagaState(saga_type="TestSaga")

        class BadSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, saga_state: SagaState) -> None:
                super().__init__(saga_state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    complete=True,
                    suspend=True,
                )

        with pytest.raises(SagaConfigurationError, match="complete.*suspend"):
            BadSaga(state)

    @pytest.mark.anyio
    async def test_complete_only_works_fine(self) -> None:
        """complete=True without suspend should work."""
        state = SagaState(saga_type="TestSaga")

        class GoodSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, saga_state: SagaState) -> None:
                super().__init__(saga_state)
                self.on(
                    OrderCreated,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    complete=True,
                )

        saga = GoodSaga(state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_suspend_only_works_fine(self) -> None:
        """suspend=True without complete should work."""
        state = SagaState(saga_type="TestSaga")

        class GoodSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, saga_state: SagaState) -> None:
                super().__init__(saga_state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    suspend=True,
                    suspend_reason="Awaiting approval",
                )

        saga = GoodSaga(state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.status == SagaStatus.SUSPENDED


# ═══════════════════════════════════════════════════════════════════════
# Issue #10 — causation_id never updated after creation
# ═══════════════════════════════════════════════════════════════════════


class TestCausationIdUpdate:
    """state.causation_id should track the last event that caused a change."""

    @pytest.mark.anyio
    async def test_causation_id_updates_on_each_event(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Each processed event should update state.causation_id."""

        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = SagaManager(
            repository=saga_repo, registry=registry, command_bus=command_bus
        )

        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event1)

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.causation_id == event1.event_id, (
            "causation_id should be set to first event"
        )

        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event2)

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.causation_id == event2.event_id, (
            "causation_id should be updated to the second event"
        )

    @pytest.mark.anyio
    async def test_causation_id_reflects_last_cause(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """After multiple events, causation_id is the last one."""

        from .conftest import FiveStepSaga

        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = SagaManager(
            repository=saga_repo, registry=registry, command_bus=command_bus
        )

        cid = uuid4()
        events = [
            OrderCreated(order_id="ORD-1", correlation_id=cid),
            ItemsReserved(order_id="ORD-1", correlation_id=cid),
        ]

        last_event = None
        for evt in events:
            await mgr.handle(evt)
            last_event = evt

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert last_event is not None
        assert state.causation_id == last_event.event_id, (
            "causation_id should reflect the most recent event"
        )


# ═══════════════════════════════════════════════════════════════════════
# Issue #11 — Suspended sagas resumed by any event
# ═══════════════════════════════════════════════════════════════════════


class TestSuspendedResumeControl:
    """Suspended sagas should support filtering which events can resume them."""

    @pytest.mark.anyio
    async def test_should_resume_hook_exists_on_base_saga(self) -> None:
        """The Saga base class should have a should_resume() method."""
        state = SagaState(saga_type="TestSaga")
        saga = Saga[SagaState](state)

        assert hasattr(saga, "should_resume"), (
            "Saga should have a should_resume() method for filtering"
        )
        # Default should return True for backward compatibility
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        result = saga.should_resume(event)
        assert result is True, "Default should_resume() must return True"

    @pytest.mark.anyio
    async def test_custom_should_resume_filters_events(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """A saga that overrides should_resume can reject certain events."""

        class SelectiveResumeSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ApprovalGranted, OrderFailed]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    step="awaiting_approval",
                    suspend=True,
                    suspend_reason="Waiting for approval",
                )
                self.on(
                    ApprovalGranted,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="confirming",
                    complete=True,
                )

            def should_resume(self, event: DomainEvent) -> bool:
                """Only ApprovalGranted can resume this saga."""
                return isinstance(event, ApprovalGranted)

        registry = SagaRegistry()
        registry.register_saga(SelectiveResumeSaga)
        mgr = SagaManager(
            repository=saga_repo, registry=registry, command_bus=command_bus
        )

        cid = uuid4()
        # Step 1: suspend the saga
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SelectiveResumeSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Step 2: send an event that should NOT resume
        await mgr.handle(
            OrderFailed(order_id="ORD-1", reason="test", correlation_id=cid)
        )

        state = await saga_repo.find_by_correlation_id(cid, "SelectiveResumeSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED, (
            "Saga should remain suspended — OrderFailed is not a resume event"
        )

        # Step 3: send the event that SHOULD resume
        await mgr.handle(ApprovalGranted(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SelectiveResumeSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED, (
            "Saga should complete after ApprovalGranted resumes it"
        )


# ═══════════════════════════════════════════════════════════════════════
# Issue #1 (whitelisted — already fixed) — Command tracing fields
# ═══════════════════════════════════════════════════════════════════════


class TestCommandTracingFields:
    """Verify that Command already has correlation_id / causation_id."""

    def test_command_has_correlation_id(self) -> None:
        cmd = ReserveItems(order_id="ORD-1")
        assert hasattr(cmd, "correlation_id")
        assert cmd.correlation_id is None  # default

    def test_command_has_causation_id(self) -> None:
        cmd = ReserveItems(order_id="ORD-1")
        assert hasattr(cmd, "causation_id")
        assert cmd.causation_id is None  # default

    def test_command_model_copy_with_tracing_fields(self) -> None:
        """model_copy(update=...) with tracing fields must work."""
        cid = uuid4()
        caid = uuid4()
        cmd = ReserveItems(order_id="ORD-1")
        traced = cmd.model_copy(update={"correlation_id": cid, "causation_id": caid})
        assert traced.correlation_id == cid
        assert traced.causation_id == caid

    def test_command_extra_forbid_does_not_block_declared_fields(self) -> None:
        """extra='forbid' should not reject declared tracing fields."""
        cmd = ReserveItems(
            order_id="ORD-1",
            correlation_id=uuid4(),
            causation_id=uuid4(),
        )
        assert cmd.correlation_id is not None
        assert cmd.causation_id is not None
