"""Edge-case and unhappy-path tests for default_timeout and step-level overrides."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.saga.saga import USE_DEFAULT_TIMEOUT, Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus

from .conftest import (
    DefaultTimeoutSaga,
    OrderCreated,
    ReserveItems,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="TestSaga", correlation_id=uuid4())


@pytest.fixture
def default_timeout_saga(saga_state: SagaState) -> DefaultTimeoutSaga:
    return DefaultTimeoutSaga(saga_state)


# ═══════════════════════════════════════════════════════════════════════
# default_timeout Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestDefaultTimeoutEdgeCases:
    """Edge cases for the default_timeout class variable and sentinel resolution."""

    # ── Sentinel identity ───────────────────────────────────────────

    def test_sentinel_is_unique(self) -> None:
        """USE_DEFAULT_TIMEOUT is unique, not equal to None or timedelta."""
        assert USE_DEFAULT_TIMEOUT is not None
        assert USE_DEFAULT_TIMEOUT is not False
        assert USE_DEFAULT_TIMEOUT != 0
        assert USE_DEFAULT_TIMEOUT is not timedelta()

    @pytest.mark.anyio
    async def test_passing_sentinel_explicitly_uses_default(
        self, saga_state: SagaState
    ) -> None:
        """Explicitly passing USE_DEFAULT_TIMEOUT is identical to omitting it."""
        now = datetime.now(UTC)

        class SentinelTestSaga(Saga[SagaState]):
            default_timeout = timedelta(minutes=30)
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    suspend=True,
                    suspend_reason="test",
                    suspend_timeout=USE_DEFAULT_TIMEOUT,  # explicit sentinel
                )

        saga = SentinelTestSaga(saga_state)
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.SUSPENDED
        assert saga.state.timeout_at is not None
        delta = saga.state.timeout_at - now  # type: ignore[operator]
        assert timedelta(minutes=29) < delta < timedelta(minutes=31)

    # ── default_timeout=None + omitted step timeout ─────────────────

    @pytest.mark.anyio
    async def test_default_none_omitted_step_timeout_is_infinite(
        self, saga_state: SagaState
    ) -> None:
        """When default_timeout is None (the base default) and step omits
        suspend_timeout, the saga suspends indefinitely."""
        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            suspend=True,
            suspend_reason="waiting",
            # suspend_timeout omitted → sentinel → Saga.default_timeout → None
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.SUSPENDED
        assert saga.state.timeout_at is None  # infinite

    # ── default_timeout=timedelta(0) ────────────────────────────────

    @pytest.mark.anyio
    async def test_default_timeout_zero_seconds(self, saga_state: SagaState) -> None:
        """default_timeout=timedelta(0) sets timeout_at to immediate expiry."""
        now = datetime.now(UTC)

        class ZeroTimeoutSaga(Saga[SagaState]):
            default_timeout = timedelta(seconds=0)
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    suspend=True,
                    suspend_reason="instant expiry",
                )

        saga = ZeroTimeoutSaga(saga_state)
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.SUSPENDED
        assert saga.state.timeout_at is not None
        # timeout_at should be approximately now (within a few seconds)
        delta = abs((saga.state.timeout_at - now).total_seconds())  # type: ignore[operator]
        assert delta < 5  # within 5 seconds

    # ── Cross-class default_timeout isolation ───────────────────────

    @pytest.mark.anyio
    async def test_different_saga_classes_have_isolated_defaults(
        self, saga_state: SagaState
    ) -> None:
        """Each saga class's default_timeout is independent."""

        class ShortTimeoutSaga(Saga[SagaState]):
            default_timeout = timedelta(minutes=5)
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    suspend=True,
                    suspend_reason="short",
                )

        class LongTimeoutSaga(Saga[SagaState]):
            default_timeout = timedelta(days=30)
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    suspend=True,
                    suspend_reason="long",
                )

        now = datetime.now(UTC)

        short_saga = ShortTimeoutSaga(saga_state)
        await short_saga.handle(OrderCreated(order_id="ORD-S", correlation_id=uuid4()))
        short_delta = short_saga.state.timeout_at - now  # type: ignore[operator]
        assert timedelta(minutes=4) < short_delta < timedelta(minutes=6)

        long_state = SagaState(saga_type="LongTimeoutSaga", correlation_id=uuid4())
        long_saga = LongTimeoutSaga(long_state)
        await long_saga.handle(OrderCreated(order_id="ORD-L", correlation_id=uuid4()))
        long_delta = long_saga.state.timeout_at - now  # type: ignore[operator]
        assert timedelta(days=29) < long_delta < timedelta(days=31)

    # ── Step override with default_timeout=None on class ────────────

    @pytest.mark.anyio
    async def test_step_explicit_none_with_class_default_none(
        self, saga_state: SagaState
    ) -> None:
        """Both class and step are None → infinite suspension."""
        saga = Saga(saga_state)  # default_timeout is None
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            suspend=True,
            suspend_reason="infinite",
            suspend_timeout=None,  # explicit None
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.SUSPENDED
        assert saga.state.timeout_at is None


# ═══════════════════════════════════════════════════════════════════════
# on_timeout + default_timeout Integration
# ═══════════════════════════════════════════════════════════════════════


class TestOnTimeoutWithDefaultTimeout:
    """on_timeout() behavior with default_timeout-configured sagas."""

    @pytest.mark.anyio
    async def test_on_timeout_uses_suspension_reason_from_callable(
        self, saga_state: SagaState
    ) -> None:
        """on_timeout() includes the suspension_reason set by the suspend step."""
        from .conftest import LogFraudFlag, TransactionFlaggedForFraud

        saga = Saga(saga_state)
        saga.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlag(
                customer_id=e.customer_id, risk_score=e.risk_score
            ),
            step="fraud_check",
            suspend=True,
            suspend_reason=lambda e: f"Risk score {e.risk_score} requires review",
            suspend_timeout=timedelta(hours=1),
        )
        await saga.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=99, correlation_id=uuid4()
            )
        )
        _ = saga.collect_commands()

        await saga.on_timeout()
        assert saga.state.status == SagaStatus.FAILED
        assert "Risk score 99 requires review" in (saga.state.error or "")

    @pytest.mark.anyio
    async def test_on_timeout_no_suspension_reason(self, saga_state: SagaState) -> None:
        """on_timeout() with no suspension_reason set."""
        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            suspend=True,
            suspend_timeout=timedelta(minutes=5),
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        _ = saga.collect_commands()

        await saga.on_timeout()
        assert saga.state.status == SagaStatus.FAILED
        assert "Saga timed out while suspended" in (saga.state.error or "")


# ═══════════════════════════════════════════════════════════════════════
# process_timeouts Integration
# ═══════════════════════════════════════════════════════════════════════


class TestProcessTimeoutsIntegration:
    """SagaManager.process_timeouts() with default_timeout-configured sagas."""

    @pytest.mark.anyio
    async def test_process_timeouts_fails_expired_saga(
        self,
    ) -> None:
        """A saga suspended with a short timeout is picked up by process_timeouts."""
        from pydomain.cqrs.saga.manager import SagaManager
        from pydomain.cqrs.saga.registry import SagaRegistry
        from pydomain.testing.fake_saga_repository import FakeSagaRepository

        from .conftest import _noop_command_bus

        repo = FakeSagaRepository()
        registry = SagaRegistry()
        registry.register_saga(DefaultTimeoutSaga)
        bus = _noop_command_bus()
        mgr = SagaManager(repository=repo, registry=registry, command_bus=bus)

        cid = uuid4()
        # Use the step with explicit 24h timeout
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await repo.find_by_correlation_id(cid, "DefaultTimeoutSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.timeout_at is not None

        # Manually expire the timeout
        state.timeout_at = datetime.now(UTC) - timedelta(seconds=1)
        await repo.save(state)

        # Process timeouts — should pick up the expired saga
        await mgr.process_timeouts(limit=10)

        state = await repo.find_by_correlation_id(cid, "DefaultTimeoutSaga")
        assert state is not None
        # Should be FAILED (no compensation stack → straight to FAILED)
        assert state.is_terminal
        assert "Saga timed out while suspended" in (state.error or "")

    @pytest.mark.anyio
    async def test_process_timeouts_skips_non_expired_saga(
        self,
    ) -> None:
        """A saga whose timeout hasn't expired yet is not touched."""
        from pydomain.cqrs.saga.manager import SagaManager
        from pydomain.cqrs.saga.registry import SagaRegistry
        from pydomain.testing.fake_saga_repository import FakeSagaRepository

        from .conftest import _noop_command_bus

        repo = FakeSagaRepository()
        registry = SagaRegistry()
        registry.register_saga(DefaultTimeoutSaga)
        bus = _noop_command_bus()
        mgr = SagaManager(repository=repo, registry=registry, command_bus=bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state_before = await repo.find_by_correlation_id(cid, "DefaultTimeoutSaga")
        assert state_before is not None
        # Timeout is 7 days in the future — not expired

        await mgr.process_timeouts(limit=10)

        state_after = await repo.find_by_correlation_id(cid, "DefaultTimeoutSaga")
        assert state_after is not None
        # Still SUSPENDED because timeout hasn't expired
        assert state_after.status == SagaStatus.SUSPENDED

    @pytest.mark.anyio
    async def test_process_timeouts_with_infinite_suspension_not_picked_up(
        self,
    ) -> None:
        """Sagas with timeout_at=None (infinite) skipped by process_timeouts."""
        from pydomain.cqrs.saga.manager import SagaManager
        from pydomain.cqrs.saga.registry import SagaRegistry
        from pydomain.testing.fake_saga_repository import FakeSagaRepository

        from .conftest import ApprovalGranted, _noop_command_bus

        repo = FakeSagaRepository()
        registry = SagaRegistry()
        registry.register_saga(DefaultTimeoutSaga)
        bus = _noop_command_bus()
        mgr = SagaManager(repository=repo, registry=registry, command_bus=bus)

        cid = uuid4()
        # Use the step with explicit None (infinite) timeout
        await mgr.handle(ApprovalGranted(order_id="ORD-1", correlation_id=cid))

        state = await repo.find_by_correlation_id(cid, "DefaultTimeoutSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.timeout_at is None  # infinite

        await mgr.process_timeouts(limit=10)

        state = await repo.find_by_correlation_id(cid, "DefaultTimeoutSaga")
        assert state is not None
        # Still SUSPENDED — infinite timeout means never expires
        assert state.status == SagaStatus.SUSPENDED
        assert state.timeout_at is None
