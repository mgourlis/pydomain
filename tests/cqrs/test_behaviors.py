from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs import Command, CommandResult
from pydomain.cqrs.behaviors import (
    AggregateLockingBehavior,
    EventIdempotencyBehavior,
    IdempotencyBehavior,
    LoggingBehavior,
    MessageContext,
    MessageKind,
    NextHandler,
    ValidationBehavior,
)
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.ddd import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure import MessageBus
from pydomain.testing import (
    FakeLockProvider,
    FakeProcessedMessageStore,
    FakeRepository,
    FakeUnitOfWork,
)

# ── Sample types ─────────────────────────────────────────────────────

# ── Next handler factories ──────────────────────────────────────────


def make_next(
    return_value: Any = None,
    exc: BaseException | None = None,
) -> NextHandler:
    """Build a NextHandler that returns *return_value* or raises *exc*."""

    async def _next() -> Any:
        if exc is not None:
            raise exc
        return return_value

    return _next


def make_trackable_next() -> tuple[NextHandler, list[bool]]:
    """Return (next_handler, flag) where flag[0] is True when next is invoked."""
    called: list[bool] = [False]

    async def _next() -> str:
        called[0] = True
        return "handled"

    return _next, called


# ── Fake message types for testing ──────────────────────────────────


@dataclass
class FakeCommand:
    value: str = "test"


@dataclass
class FakeEvent:
    entity_id: str = "123"


# ── Fake handler ────────────────────────────────────────────────────


async def fake_handler(message: Any, uow: Any = None) -> str:
    return "handled"


# ══════════════════════════════════════════════════════════════════════
# LoggingBehavior tests
# ══════════════════════════════════════════════════════════════════════


class TestLoggingBehavior:
    @pytest.mark.anyio
    async def test_entry_log_contains_kind_type_and_handler_name(
        self,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.INFO)
        behavior = LoggingBehavior()
        ctx = MessageContext(
            message=FakeCommand(value="hello"),
            handler=fake_handler,
            kind=MessageKind.COMMAND,
        )

        await behavior.handle(ctx, make_next(return_value="done"))

        assert len(caplog.records) >= 1
        entry = caplog.records[0]
        assert entry.levelname == "INFO"
        assert entry.name == "pydomain.pipeline"
        msg = entry.getMessage()
        assert "Processing" in msg
        assert "COMMAND" in msg
        assert "FakeCommand" in msg
        assert "fake_handler" in msg

    @pytest.mark.anyio
    async def test_entry_log_includes_correlation_and_causation_ids(
        self,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.INFO)
        behavior = LoggingBehavior()
        corr_id = uuid4()
        caus_id = uuid4()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            kind=MessageKind.COMMAND,
            correlation_id=corr_id,
            causation_id=caus_id,
            metadata={"command_id": "cmd-123"},
        )

        await behavior.handle(ctx, make_next(return_value="done"))

        entry = caplog.records[0]
        msg = entry.getMessage()
        assert str(corr_id) in msg
        assert str(caus_id) in msg
        assert "cmd-123" in msg

    @pytest.mark.anyio
    async def test_entry_log_shows_na_for_missing_ids(self, caplog: Any) -> None:
        caplog.set_level(logging.INFO)
        behavior = LoggingBehavior()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            kind=MessageKind.EVENT,
        )

        await behavior.handle(ctx, make_next(return_value="done"))

        entry = caplog.records[0]
        msg = entry.getMessage()
        assert "correlation=N/A" in msg
        assert "causation=N/A" in msg
        assert "command=N/A" in msg

    @pytest.mark.anyio
    async def test_exit_log_on_success_shows_duration_in_ms(self, caplog: Any) -> None:
        caplog.set_level(logging.INFO)
        behavior = LoggingBehavior()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            kind=MessageKind.COMMAND,
        )

        await behavior.handle(ctx, make_next(return_value="done"))

        assert len(caplog.records) == 2
        exit_record = caplog.records[1]
        assert exit_record.levelname == "INFO"
        msg = exit_record.getMessage()
        assert msg.startswith("Completed COMMAND FakeCommand in ")
        assert msg.endswith("ms")

    @pytest.mark.anyio
    async def test_exit_log_on_failure_logs_error_with_exception_type(
        self,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.INFO)
        behavior = LoggingBehavior()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            kind=MessageKind.COMMAND,
        )

        with pytest.raises(ValueError):
            await behavior.handle(ctx, make_next(exc=ValueError("boom")))

        assert len(caplog.records) == 2  # entry INFO + error ERROR
        error_record = caplog.records[1]
        assert error_record.levelname == "ERROR"
        msg = error_record.getMessage()
        assert msg.startswith("Failed COMMAND FakeCommand after ")
        # Message format: "Failed ... after X.XXms: ExceptionType"
        assert "ms: ValueError" in msg

    @pytest.mark.anyio
    async def test_exception_is_re_raised(self) -> None:
        behavior = LoggingBehavior()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            kind=MessageKind.COMMAND,
        )

        with pytest.raises(RuntimeError, match="fail"):
            await behavior.handle(ctx, make_next(exc=RuntimeError("fail")))

    @pytest.mark.anyio
    async def test_result_returned_on_success(self) -> None:
        behavior = LoggingBehavior()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            kind=MessageKind.COMMAND,
        )

        result = await behavior.handle(ctx, make_next(return_value={"key": "value"}))
        assert result == {"key": "value"}

    @pytest.mark.anyio
    async def test_payload_formatter_called_and_included_via_extra(
        self,
        caplog: Any,
    ) -> None:
        caplog.set_level(logging.INFO)

        def payload_formatter(msg: FakeCommand) -> dict:
            return {"original_value": msg.value, "extra_info": 42}

        behavior = LoggingBehavior(payload_formatter=payload_formatter)
        ctx = MessageContext(
            message=FakeCommand(value="hello"),
            handler=fake_handler,
            kind=MessageKind.COMMAND,
        )

        await behavior.handle(ctx, make_next(return_value="done"))

        entry = caplog.records[0]
        assert hasattr(entry, "payload")
        assert entry.payload == {"original_value": "hello", "extra_info": 42}


# ══════════════════════════════════════════════════════════════════════
# ValidationBehavior tests
# ══════════════════════════════════════════════════════════════════════


class TestValidationBehavior:
    @pytest.mark.anyio
    async def test_sync_validator_passes_handler_called(self) -> None:
        behavior = ValidationBehavior()
        behavior.register(FakeCommand, lambda msg: None)

        _next, called = make_trackable_next()
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        result = await behavior.handle(ctx, _next)

        assert called[0], "next handler should have been called"
        assert result == "handled"

    @pytest.mark.anyio
    async def test_sync_validator_raises_handler_not_called(self) -> None:
        def validate(msg: FakeCommand) -> None:
            raise ValueError("invalid")

        behavior = ValidationBehavior()
        behavior.register(FakeCommand, validate)

        _next, called = make_trackable_next()
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        with pytest.raises(ValueError, match="invalid"):
            await behavior.handle(ctx, _next)

        assert not called[0], "next handler should NOT have been called"

    @pytest.mark.anyio
    async def test_async_validator_passes_handler_called(self) -> None:
        async def validate(msg: FakeCommand) -> None:
            pass

        behavior = ValidationBehavior()
        behavior.register(FakeCommand, validate)

        _next, called = make_trackable_next()
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        result = await behavior.handle(ctx, _next)

        assert called[0], "next handler should have been called"
        assert result == "handled"

    @pytest.mark.anyio
    async def test_async_validator_raises_handler_not_called(self) -> None:
        async def validate(msg: FakeCommand) -> None:
            raise ValueError("async invalid")

        behavior = ValidationBehavior()
        behavior.register(FakeCommand, validate)

        _next, called = make_trackable_next()
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        with pytest.raises(ValueError, match="async invalid"):
            await behavior.handle(ctx, _next)

        assert not called[0], "next handler should NOT have been called"

    @pytest.mark.anyio
    async def test_multiple_validators_run_in_registration_order(self) -> None:
        order: list[str] = []

        behavior = ValidationBehavior()
        behavior.register(FakeCommand, lambda msg: order.append("first"))
        behavior.register(FakeCommand, lambda msg: order.append("second"))
        behavior.register(FakeCommand, lambda msg: order.append("third"))

        _next, called = make_trackable_next()
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        result = await behavior.handle(ctx, _next)

        assert order == ["first", "second", "third"]
        assert called[0], "next handler should have been called"
        assert result == "handled"

    @pytest.mark.anyio
    async def test_validators_for_one_message_type_do_not_affect_another(self) -> None:
        def should_not_be_called(msg: FakeCommand) -> None:
            raise AssertionError("Validator should not be called for FakeEvent")

        behavior = ValidationBehavior()
        behavior.register(FakeCommand, should_not_be_called)

        _next, called = make_trackable_next()
        ctx = MessageContext(message=FakeEvent(), handler=fake_handler)

        result = await behavior.handle(ctx, _next)

        assert called[0], "next handler should have been called"
        assert result == "handled"

    @pytest.mark.anyio
    async def test_no_validators_registered_handler_called_directly(self) -> None:
        behavior = ValidationBehavior()

        _next, called = make_trackable_next()
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        result = await behavior.handle(ctx, _next)

        assert called[0], "next handler should have been called"
        assert result == "handled"

    @pytest.mark.anyio
    async def test_result_returned_after_validation_passes(self) -> None:
        behavior = ValidationBehavior()
        behavior.register(FakeCommand, lambda msg: None)

        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        result = await behavior.handle(ctx, make_next(return_value={"success": True}))
        assert result == {"success": True}

    # ── Registration edge case ──────────────────────────────────────────

    @pytest.mark.anyio
    async def test_register_multiple_validators_are_appended(self) -> None:
        """Registering multiple validators for the same type appends them."""
        behavior = ValidationBehavior()
        calls: list[int] = []

        behavior.register(FakeCommand, lambda msg: calls.append(1))
        behavior.register(FakeCommand, lambda msg: calls.append(2))
        behavior.register(FakeCommand, lambda msg: calls.append(3))

        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)
        await behavior.handle(ctx, make_next(return_value=None))

        assert calls == [1, 2, 3]

    @pytest.mark.anyio
    async def test_constructor_accepts_pre_populated_validators(self) -> None:
        """Validators can be injected via the constructor."""
        calls: list[str] = []

        validators: dict[type, list[Callable]] = {
            FakeCommand: [lambda msg: calls.append("a")]
        }
        behavior = ValidationBehavior(validators=validators)
        behavior.register(FakeCommand, lambda msg: calls.append("b"))

        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)
        await behavior.handle(ctx, make_next(return_value=None))

        assert calls == ["a", "b"]

    @pytest.mark.anyio
    async def test_first_validator_failure_skips_remaining_validators(self) -> None:
        """When a validator raises, subsequent validators should not run."""
        calls: list[str] = []

        def fail_first(msg: FakeCommand) -> None:
            calls.append("first")
            raise ValueError("first failed")

        behavior = ValidationBehavior()
        behavior.register(FakeCommand, fail_first)
        behavior.register(FakeCommand, lambda msg: calls.append("second"))

        _next, called = make_trackable_next()
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        with pytest.raises(ValueError, match="first failed"):
            await behavior.handle(ctx, _next)

        assert calls == ["first"]
        assert not called[0], "next handler should NOT have been called"

    @pytest.mark.anyio
    async def test_message_kind_is_not_checked_by_validation(self) -> None:
        """ValidationBehavior uses message type, not MessageKind."""
        order: list[str] = []

        behavior = ValidationBehavior()
        behavior.register(FakeCommand, lambda msg: order.append("called"))

        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            kind=MessageKind.QUERY,
        )

        await behavior.handle(ctx, make_next(return_value="ok"))

        assert order == ["called"]


# ══════════════════════════════════════════════════════════════════════
# AggregateLockingBehavior tests
# ══════════════════════════════════════════════════════════════════════


class RecordingLockProvider:
    """Wraps ``FakeLockProvider`` and records acquire/release order."""

    def __init__(self) -> None:
        self._inner = FakeLockProvider()
        self.acquired: list[str] = []
        self.released: list[str] = []

    async def acquire(self, key: str) -> None:
        self.acquired.append(key)
        await self._inner.acquire(key)

    async def release(self, key: str) -> None:
        self.released.append(key)
        await self._inner.release(key)


class TestAggregateLockingBehavior:
    """Tests for ``AggregateLockingBehavior`` pipeline behavior.

    Covers lock acquisition ordering, release ordering, error recovery,
    empty-key short-circuit, and result propagation.
    """

    @pytest.mark.anyio
    async def test_single_key_lock_acquired_handler_runs_lock_released(
        self,
    ) -> None:
        """When a single lock key is resolved, it is acquired before the
        handler runs and released afterward."""
        provider = FakeLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(FakeCommand, lambda msg: ["account:123"])

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        _next, called = make_trackable_next()
        result = await behavior.handle(ctx, _next)

        assert called[0], "handler should have been called"
        assert result == "handled"

    @pytest.mark.anyio
    async def test_multiple_keys_acquired_in_sorted_order(self) -> None:
        """Lock keys are sorted before acquisition to prevent deadlocks."""
        provider = RecordingLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(
            FakeCommand,
            lambda msg: ["account:B", "account:A", "account:C"],
        )

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        await behavior.handle(ctx, make_next(return_value=None))

        assert provider.acquired == ["account:A", "account:B", "account:C"]

    @pytest.mark.anyio
    async def test_keys_released_in_reverse_order(self) -> None:
        """Locks are released in reverse acquisition order."""
        provider = RecordingLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(
            FakeCommand,
            lambda msg: ["account:B", "account:A", "account:C"],
        )

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        await behavior.handle(ctx, make_next(return_value=None))

        assert provider.released == ["account:C", "account:B", "account:A"]

    @pytest.mark.anyio
    async def test_handler_exception_still_releases_locks(self) -> None:
        """When the handler raises, all locks are released via finally."""
        provider = RecordingLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(FakeCommand, lambda msg: ["account:X"])

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        with pytest.raises(RuntimeError, match="handler failed"):
            await behavior.handle(
                ctx,
                make_next(exc=RuntimeError("handler failed")),
            )

        assert provider.released == ["account:X"]

    @pytest.mark.anyio
    async def test_empty_key_list_skips_locking(self) -> None:
        """When the resolver returns an empty list, locking is skipped and
        the handler is called directly."""
        provider = RecordingLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(FakeCommand, lambda msg: [])

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        _next, called = make_trackable_next()
        result = await behavior.handle(ctx, _next)

        assert provider.acquired == []
        assert provider.released == []
        assert called[0], "handler should have been called"
        assert result == "handled"

    @pytest.mark.anyio
    async def test_acquire_failure_releases_already_acquired_locks(self) -> None:
        """When acquiring a lock fails, previously acquired locks are
        released before the exception propagates."""

        class FailingLockProvider:
            """Simulates a provider that fails on the second acquire."""

            def __init__(self) -> None:
                self._inner = FakeLockProvider()
                self.acquired: list[str] = []
                self.released: list[str] = []

            async def acquire(self, key: str) -> None:
                if key == "account:B":
                    raise RuntimeError(f"Failed to acquire lock: {key}")
                self.acquired.append(key)
                await self._inner.acquire(key)

            async def release(self, key: str) -> None:
                self.released.append(key)
                await self._inner.release(key)

        provider = FailingLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(
            FakeCommand,
            lambda msg: ["account:A", "account:B", "account:C"],
        )

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        with pytest.raises(RuntimeError, match="Failed to acquire lock: account:B"):
            await behavior.handle(ctx, make_next(return_value=None))

        assert provider.acquired == ["account:A"]
        assert provider.released == ["account:A"]

    @pytest.mark.anyio
    async def test_handler_not_called_when_acquire_fails(self) -> None:
        """When acquire raises, the handler should never be invoked."""

        class FailingLockProvider:
            async def acquire(self, key: str) -> None:
                raise RuntimeError(f"Failed: {key}")

            async def release(self, key: str) -> None:
                pass

        provider = FailingLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(FakeCommand, lambda msg: ["account:A"])

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        _next, called = make_trackable_next()

        with pytest.raises(RuntimeError, match="Failed: account:A"):
            await behavior.handle(ctx, _next)

        assert not called[0], "handler should NOT have been called"

    @pytest.mark.anyio
    async def test_result_from_handler_is_returned(self) -> None:
        """The return value from the handler propagates through the
        behavior."""
        provider = FakeLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(FakeCommand, lambda msg: ["account:123"])

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        result = await behavior.handle(
            ctx,
            make_next(return_value={"success": True}),
        )
        assert result == {"success": True}

    @pytest.mark.anyio
    async def test_duplicate_keys_are_deduplicated(self) -> None:
        """When resolver returns duplicate keys, they are collapsed to
        a single acquire/release to prevent deadlocks."""
        provider = RecordingLockProvider()
        resolver = DictLockKeyResolver()
        resolver.register(FakeCommand, lambda msg: ["account:123"])
        resolver.register(FakeCommand, lambda msg: ["account:123"])

        behavior = AggregateLockingBehavior(provider, resolver)
        ctx = MessageContext(message=FakeCommand(), handler=fake_handler)

        await behavior.handle(ctx, make_next(return_value=None))

        assert provider.acquired == ["account:123"]
        assert provider.released == ["account:123"]


# ══════════════════════════════════════════════════════════════════════
# IdempotencyBehavior tests
# ══════════════════════════════════════════════════════════════════════


class TestIdempotencyBehavior:
    """Tests for the IdempotencyBehavior pipeline behavior."""

    @pytest.mark.anyio
    async def test_new_command_passes_through_and_caches_result(self) -> None:
        """First time a command ID is seen: handler runs, result is cached."""
        store = FakeProcessedMessageStore()
        behavior = IdempotencyBehavior(store)
        command_id = uuid4()

        _next, called = make_trackable_next()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            metadata={"command_id": command_id},
        )

        result = await behavior.handle(ctx, _next)

        assert called[0], "next handler should have been called"
        assert result == "handled"
        assert await store.contains(command_id), "result should have been cached"
        assert await store.get(command_id) == "handled", "cached result should match"

    @pytest.mark.anyio
    async def test_duplicate_command_returns_cached_result(self) -> None:
        """Second time: cached result returned, inner handler NOT called."""
        store = FakeProcessedMessageStore()
        behavior = IdempotencyBehavior(store)
        command_id = uuid4()
        cached_result = {"status": "already_done"}

        await store.set(command_id, cached_result)

        _next, called = make_trackable_next()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            metadata={"command_id": command_id},
        )

        result = await behavior.handle(ctx, _next)

        assert not called[0], "next handler should NOT have been called"
        assert result == cached_result

    @pytest.mark.anyio
    async def test_missing_command_id_passes_through(self) -> None:
        """When ctx.metadata has no 'command_id', delegate to next() directly."""
        store = FakeProcessedMessageStore()
        behavior = IdempotencyBehavior(store)

        _next, called = make_trackable_next()
        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
        )

        result = await behavior.handle(ctx, _next)

        assert called[0], "next handler should have been called"
        assert result == "handled"

    @pytest.mark.anyio
    async def test_cached_none_result_is_returned(self) -> None:
        """A handler that returns None -- the cached result is correctly returned."""
        store = FakeProcessedMessageStore()
        behavior = IdempotencyBehavior(store)
        command_id = uuid4()

        called: list[bool] = [False]

        async def _next_returns_none() -> Any:
            called[0] = True
            return None

        ctx = MessageContext(
            message=FakeCommand(),
            handler=fake_handler,
            metadata={"command_id": command_id},
        )

        # First call: handler returns None, result is cached
        result = await behavior.handle(ctx, _next_returns_none)
        assert called[0], "next handler should have been called on first invocation"
        assert result is None
        assert await store.contains(command_id), "None result should have been cached"

        # Second call: cached None should be returned, handler NOT called
        called_again: list[bool] = [False]

        async def _next_should_not_be_called() -> Any:
            called_again[0] = True
            return "should not reach here"

        result2 = await behavior.handle(ctx, _next_should_not_be_called)
        assert not called_again[0], (
            "next handler should NOT have been called on duplicate"
        )
        assert result2 is None, "cached None should be returned"


# ════════════════════════════════════════════════════════════════════════════
# Event idempotency
# ════════════════════════════════════════════════════════════════════════════


class _SampleEvent(DomainEvent):
    """Test event type for EventIdempotencyBehavior tests."""

    data: str


class TestEventIdempotencyBehavior:
    """EventIdempotencyBehavior skips duplicate events."""

    @pytest.mark.anyio
    async def test_new_event_passes_through(self) -> None:
        """A causation_id not in the store passes through to the handler."""
        store = FakeProcessedMessageStore()
        behavior = EventIdempotencyBehavior(store)
        event = _SampleEvent(data="first", causation_id=uuid4())

        async def handler(event: _SampleEvent) -> None:
            pass

        ctx = MessageContext(
            message=event,
            handler=handler,
            kind=MessageKind.EVENT,
        )

        result = await behavior.handle(ctx, make_next(return_value="ok"))

        assert result == "ok"

    @pytest.mark.anyio
    async def test_duplicate_event_skipped(self) -> None:
        """Same causation_id causes the handler to be skipped."""
        store = FakeProcessedMessageStore()
        behavior = EventIdempotencyBehavior(store)
        causation_id = uuid4()

        # Process first event — goes through
        event1 = _SampleEvent(data="first", causation_id=causation_id)

        async def handler(event: _SampleEvent) -> None:
            pass

        ctx1 = MessageContext(
            message=event1,
            handler=handler,
            kind=MessageKind.EVENT,
        )
        await behavior.handle(ctx1, make_next(return_value="ok"))

        # Second event with same causation_id — gets skipped
        event2 = _SampleEvent(data="second", causation_id=causation_id)
        ctx2 = MessageContext(
            message=event2,
            handler=handler,
            kind=MessageKind.EVENT,
        )
        result = await behavior.handle(ctx2, make_next(return_value="ok"))

        assert result is None

    @pytest.mark.anyio
    async def test_no_causation_id_passes_through(self) -> None:
        """An event with causation_id=None always passes through."""
        store = FakeProcessedMessageStore()
        behavior = EventIdempotencyBehavior(store)
        event = _SampleEvent(data="test")  # causation_id is None

        async def handler(event: _SampleEvent) -> None:
            pass

        ctx = MessageContext(
            message=event,
            handler=handler,
            kind=MessageKind.EVENT,
        )

        # Calling twice — both should pass through
        result1 = await behavior.handle(ctx, make_next(return_value="ok"))
        result2 = await behavior.handle(ctx, make_next(return_value="ok"))

        assert result1 == "ok"
        assert result2 == "ok"


# ── CQRS-05: idempotency keys collide (review probes of PR #118) ─────────


class _Placed(DomainEvent):
    order_id: UUID


class _Order(AggregateRoot[UUID]):
    def place(self) -> None:
        self._add_event(_Placed(order_id=self.id))


class _PlaceResult(CommandResult):
    pass


class _Place(Command[_PlaceResult]):
    orders: int = 1


async def _handled_events(*, shared_store: bool, orders: int) -> list[UUID]:
    """Dispatch one command that places *orders* orders; return the handled events."""
    repo: FakeRepository[_Order, UUID] = FakeRepository()
    command_store = FakeProcessedMessageStore()
    event_store = command_store if shared_store else FakeProcessedMessageStore()
    handled: list[UUID] = []

    async def place(command: _Place, uow: Any) -> _PlaceResult:
        for _ in range(command.orders):
            order = _Order(id=uuid4())
            order.place()
            await repo.save(order)
        return _PlaceResult()

    async def on_placed(event: _Placed) -> None:
        handled.append(event.order_id)

    bus = MessageBus()
    bus.register_command(
        _Place,
        place,
        lambda: FakeUnitOfWork(repository=repo),
        behaviors=[IdempotencyBehavior(command_store)],
    )
    bus.register_event(
        _Placed, on_placed, behaviors=[EventIdempotencyBehavior(event_store)]
    )
    await bus.dispatch(_Place(orders=orders))
    return handled


CQRS_05 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-126 review: CQRS-05, event keys reuse the command id (W2-2)",
)


@pytest.mark.anyio
@CQRS_05
async def test_a_store_shared_with_command_idempotency_keeps_the_events() -> None:
    """add-idempotency.md lets one store serve both behaviors."""
    assert len(await _handled_events(shared_store=True, orders=1)) == 1


@pytest.mark.anyio
@CQRS_05
async def test_every_event_of_one_command_is_handled() -> None:
    assert len(await _handled_events(shared_store=False, orders=2)) == 2
