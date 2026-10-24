"""Comprehensive tests for the EventBus.

Covers registration, dispatch, pipeline behaviors, and failure isolation.
"""

from __future__ import annotations

import copy
import functools
import logging
import pickle
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs import EventBus
from pydomain.cqrs import exceptions as cqrs_exceptions
from pydomain.cqrs.behaviors import (
    EventIdempotencyBehavior,
    MessageContext,
    MessageKind,
    NextHandler,
)
from pydomain.cqrs.exceptions import CQRSError, EventHandlersFailed
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeProcessedMessageStore
from tests.cqrs.test_event_idempotency import _key
from tests.cqrs.test_idempotency_reservation import _is_free
from tests.support.content_sentinel import CONTENT_SENTINEL

# ── Sample test types ────────────────────────────────────────────────────────


class _Evt(DomainEvent):
    data: str


class _OtherEvt(DomainEvent):
    value: int


# ════════════════════════════════════════════════════════════════════════════
# Registration
# ════════════════════════════════════════════════════════════════════════════


class TestRegister:
    """EventBus.register()"""

    @pytest.mark.anyio
    async def test_register_single_handler(self) -> None:
        """A single handler is called when event is dispatched."""
        bus = EventBus()
        called: list[bool] = [False]

        async def handler(event: _Evt) -> None:
            called[0] = True

        bus.register(_Evt, handler)
        await bus.dispatch(_Evt(data="test"))

        assert called[0] is True

    @pytest.mark.anyio
    async def test_register_multiple_handlers(self) -> None:
        """Multiple handlers for the same event type are all called."""
        bus = EventBus()
        results: list[int] = []

        async def handler1(event: _Evt) -> None:
            results.append(1)

        async def handler2(event: _Evt) -> None:
            results.append(2)

        bus.register(_Evt, handler1)
        bus.register(_Evt, handler2)
        await bus.dispatch(_Evt(data="test"))

        assert results == [1, 2]

    @pytest.mark.anyio
    async def test_register_multiple_types(self) -> None:
        """Handlers for different event types are isolated."""
        bus = EventBus()
        results: list[str] = []

        async def evt_handler(event: _Evt) -> None:
            results.append(f"evt:{event.data}")

        async def other_handler(event: _OtherEvt) -> None:
            results.append(f"other:{event.value}")

        bus.register(_Evt, evt_handler)
        bus.register(_OtherEvt, other_handler)

        await bus.dispatch(_Evt(data="hello"))
        await bus.dispatch(_OtherEvt(value=42))

        assert results == ["evt:hello", "other:42"]

    @pytest.mark.anyio
    async def test_register_with_behaviors(self) -> None:
        """Behaviors wrap the handler in onion order."""
        bus = EventBus()
        trace: list[str] = []

        class SpyBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                trace.append("before")
                result = await next()
                trace.append("after")
                return result

        async def handler(event: _Evt) -> None:
            trace.append("handler")

        bus.register(_Evt, handler, behaviors=[SpyBehavior()])
        await bus.dispatch(_Evt(data="test"))

        assert trace == ["before", "handler", "after"]


# ════════════════════════════════════════════════════════════════════════════
# Dispatch (single event)
# ════════════════════════════════════════════════════════════════════════════


class TestDispatch:
    """EventBus.dispatch()"""

    @pytest.mark.anyio
    async def test_dispatch_calls_handler(self) -> None:
        """dispatch() invokes the registered handler."""
        bus = EventBus()
        received: list[_Evt] = []

        async def handler(event: _Evt) -> None:
            received.append(event)

        bus.register(_Evt, handler)
        await bus.dispatch(_Evt(data="hello"))

        assert len(received) == 1
        assert received[0].data == "hello"

    @pytest.mark.anyio
    async def test_dispatch_returns_none(self) -> None:
        """dispatch() returns None for events."""
        bus = EventBus()

        async def handler(event: _Evt) -> None:
            pass

        bus.register(_Evt, handler)
        result = await bus.dispatch(_Evt(data="test"))

        assert result is None

    @pytest.mark.anyio
    async def test_dispatch_no_handlers(self) -> None:
        """Event with no registered handler is silently ignored."""
        bus = EventBus()
        result = await bus.dispatch(_Evt(data="no-handler"))

        assert result is None

    @pytest.mark.anyio
    async def test_dispatch_multiple_handlers_in_order(self) -> None:
        """Handlers are called in registration order."""
        bus = EventBus()
        order: list[int] = []

        async def handler1(event: _Evt) -> None:
            order.append(1)

        async def handler2(event: _Evt) -> None:
            order.append(2)

        async def handler3(event: _Evt) -> None:
            order.append(3)

        bus.register(_Evt, handler1)
        bus.register(_Evt, handler2)
        bus.register(_Evt, handler3)

        await bus.dispatch(_Evt(data="test"))

        assert order == [1, 2, 3]


# ════════════════════════════════════════════════════════════════════════════
# Dispatch (batch)
# ════════════════════════════════════════════════════════════════════════════


class TestDispatchMany:
    """EventBus.dispatch_many()"""

    @pytest.mark.anyio
    async def test_dispatch_many_calls_all_handlers(self) -> None:
        """dispatch_many() dispatches each event in the list."""
        bus = EventBus()
        results: list[str] = []

        async def handler(event: _Evt) -> None:
            results.append(event.data)

        bus.register(_Evt, handler)
        await bus.dispatch_many(
            [
                _Evt(data="a"),
                _Evt(data="b"),
                _Evt(data="c"),
            ]
        )

        assert results == ["a", "b", "c"]


# ════════════════════════════════════════════════════════════════════════════
# Failure isolation
# ════════════════════════════════════════════════════════════════════════════


class TestFailureIsolation:
    """Event handler failures are isolated: every handler runs.

    DCE-152 (ADR-081 point 1, B-193) rewrote these three tests: until then
    they pinned that ``dispatch`` swallows the failures. It now raises
    ``EventHandlersFailed`` once every handler has run.
    """

    @pytest.mark.anyio
    async def test_failure_logged_then_raised_after_the_others_ran(
        self, caplog: Any
    ) -> None:
        """A failing handler is logged and does not stop other handlers; the
        dispatch then raises the group with that one failure. DCE-153 (B-193,
        ADR-081 point 4): the line describes the failure and carries a
        value-free stack, and the record has no ``exc_info``."""
        bus = EventBus()
        results: list[str] = []

        async def failing_handler(event: _Evt) -> None:
            results.append("fail")
            raise ValueError("handler failure")

        async def success_handler(event: _Evt) -> None:
            results.append("ok")

        bus.register(_Evt, failing_handler)
        bus.register(_Evt, success_handler)

        caplog.set_level(logging.ERROR, logger="pydomain.event_bus")
        with pytest.raises(EventHandlersFailed) as raised:
            await bus.dispatch(_Evt(data="test"))

        assert results == ["fail", "ok"]
        (failure,) = raised.value.exceptions
        assert isinstance(failure, ValueError)
        assert str(failure) == "handler failure"

        error_logs = [
            r
            for r in caplog.records
            if "Event handler" in r.getMessage() and r.levelno == logging.ERROR
        ]
        assert len(error_logs) == 1
        first, *stack = error_logs[0].getMessage().split("\n")
        assert first == (
            f"Event handler {failing_handler.__qualname__} failed for _Evt: ValueError"
        )
        assert stack[0] == "ValueError"
        assert stack[-1].endswith(", in failing_handler")
        assert error_logs[0].exc_info is None

    @pytest.mark.anyio
    async def test_multiple_failures_all_isolated(self) -> None:
        """Multiple failing handlers all run, and so do the others; the group
        holds both failures in registration order."""
        bus = EventBus()
        results: list[str] = []

        async def fail1(event: _Evt) -> None:
            results.append("fail1")
            raise ValueError("first")

        async def ok(event: _Evt) -> None:
            results.append("ok")

        async def fail2(event: _Evt) -> None:
            results.append("fail2")
            raise RuntimeError("second")

        bus.register(_Evt, fail1)
        bus.register(_Evt, ok)
        bus.register(_Evt, fail2)

        with pytest.raises(EventHandlersFailed) as raised:
            await bus.dispatch(_Evt(data="test"))

        assert results == ["fail1", "ok", "fail2"]
        assert [(type(e), str(e)) for e in raised.value.exceptions] == [
            (ValueError, "first"),
            (RuntimeError, "second"),
        ]

    @pytest.mark.anyio
    async def test_failure_in_first_handler_does_not_block_others(self) -> None:
        """First handler failing does not stop subsequent handlers; the group
        holds that one failure."""
        bus = EventBus()
        results: list[str] = []

        async def fail_first(event: _Evt) -> None:
            results.append("fail")
            raise ValueError("boom")

        async def second(event: _Evt) -> None:
            results.append("second")

        async def third(event: _Evt) -> None:
            results.append("third")

        bus.register(_Evt, fail_first)
        bus.register(_Evt, second)
        bus.register(_Evt, third)

        with pytest.raises(EventHandlersFailed) as raised:
            await bus.dispatch(_Evt(data="test"))

        assert results == ["fail", "second", "third"]
        assert [(type(e), str(e)) for e in raised.value.exceptions] == [
            (ValueError, "boom")
        ]


# ════════════════════════════════════════════════════════════════════════════
# DCE-152: direct dispatch raises EventHandlersFailed (ADR-081 points 1, 2)
# ════════════════════════════════════════════════════════════════════════════
#
# Red tests reach ``EventHandlersFailed`` by attribute access on the module, so
# this file collects before the name exists (frame, "Red and guard tests").


async def _refuse(event: _Evt, *, tag: str = "") -> None:
    raise ValueError(f"refused {tag}")


class _NamedHandlers:
    async def on_evt(self, event: _Evt) -> None:
        raise ValueError("refused")


class _CallableHandler:
    """A callable instance whose ``repr()`` would carry content."""

    def __repr__(self) -> str:
        return f"_CallableHandler({CONTENT_SENTINEL})"

    async def __call__(self, event: _Evt, *, tag: str = "") -> None:
        raise ValueError("refused")


class _ForwardingHandler:
    """A callable instance whose ``__getattr__`` answers with data, as a
    proxy does: its ``__qualname__`` would be content."""

    def __getattr__(self, name: str) -> str:
        return CONTENT_SENTINEL

    async def __call__(self, event: _Evt) -> None:
        raise ValueError("refused")


class _DescriptorForwardingHandler(_ForwardingHandler):
    """As ``_ForwardingHandler``, and its class defines ``__get__``, which
    makes ``inspect.isroutine`` take it for a method descriptor."""

    def __get__(self, instance: object, owner: type | None = None) -> Any:
        return self


def _unflattened_partial() -> functools.partial[Any]:
    """A partial of a partial that CPython does not flatten: the inner one
    carries an attribute, so the unwrap must loop."""
    inner = functools.partial(_refuse, tag="a")
    inner.marker = "synthetic"  # type: ignore[attr-defined]
    outer = functools.partial(inner, tag="b")
    assert outer.func is inner
    return outer


@pytest.mark.parametrize(
    ("handler", "name"),
    [
        pytest.param(_NamedHandlers().on_evt, "_NamedHandlers.on_evt", id="method"),
        pytest.param(functools.partial(_refuse, tag="a"), "_refuse", id="partial"),
        pytest.param(
            functools.partial(functools.partial(_refuse, tag="a"), tag="b"),
            "_refuse",
            id="partial-of-partial",
        ),
        pytest.param(
            _unflattened_partial(), "_refuse", id="partial-of-unflattened-partial"
        ),
        pytest.param(
            _ForwardingHandler(),
            "_ForwardingHandler",
            id="callable-instance-with-getattr",
        ),
        pytest.param(
            _DescriptorForwardingHandler(),
            "_DescriptorForwardingHandler",
            id="descriptor-instance-with-getattr",
        ),
        pytest.param(
            functools.partial(_CallableHandler(), tag="a"),
            "_CallableHandler",
            id="partial-of-callable-instance",
        ),
    ],
)
@pytest.mark.anyio
async def test_failure_log_names_a_handler_by_qualname(
    caplog: Any, handler: Any, name: str
) -> None:
    """B-201: ``__qualname__``, a ``partial`` unwrapped recursively, else the
    type's ``__qualname__``; never ``repr()`` or ``str()``."""
    bus = EventBus()
    bus.register(_Evt, handler)
    caplog.set_level(logging.ERROR, logger="pydomain.event_bus")

    await bus.dispatch_many([_Evt(data="test")])

    (record,) = caplog.records
    assert record.getMessage().split("\n")[0] == (
        f"Event handler {name} failed for _Evt: ValueError"
    )
    assert CONTENT_SENTINEL not in record.getMessage()


class _Refused(Exception):
    """A handler refuses the event."""


async def _raised(awaitable: Awaitable[Any]) -> BaseException | None:
    """Await *awaitable*; return what it raised, ``None`` if it returned."""
    try:
        await awaitable
    except BaseException as error:
        return error
    return None


def _failing_bus(*errors: Exception) -> tuple[EventBus, list[str]]:
    """A bus whose ``_Evt`` handlers raise *errors* in turn, with an ``ok``
    handler between each pair; the list records which handlers ran."""
    bus = EventBus()
    ran: list[str] = []

    def failing(error: Exception, name: str) -> Any:
        async def handler(event: _Evt) -> None:
            ran.append(name)
            raise error

        return handler

    async def ok(event: _Evt) -> None:
        ran.append("ok")

    for index, error in enumerate(errors):
        if index:
            bus.register(_Evt, ok)
        bus.register(_Evt, failing(error, f"fail{index + 1}"))
    return bus, ran


class TestDirectDispatchRaises:
    """``EventBus.dispatch`` raises once every handler has run (B-180)."""

    @pytest.mark.anyio
    async def test_direct_dispatch_raises_after_every_handler_ran(self) -> None:
        bus, ran = _failing_bus(ValueError("first"), RuntimeError("second"))

        with pytest.raises(cqrs_exceptions.EventHandlersFailed):
            await bus.dispatch(_Evt(data="test"))

        assert ran == ["fail1", "ok", "fail2"]

    @pytest.mark.anyio
    async def test_handlers_failed_holds_each_handler_exception_unchanged(
        self,
    ) -> None:
        """The members are the handlers' own objects, in registration order."""
        first, second = ValueError("first"), RuntimeError("second")
        bus, _ = _failing_bus(first, second)

        group = await _raised(bus.dispatch(_Evt(data="test")))

        assert isinstance(group, cqrs_exceptions.EventHandlersFailed)
        assert len(group.exceptions) == 2
        assert group.exceptions[0] is first
        assert group.exceptions[1] is second

    @pytest.mark.anyio
    async def test_handlers_failed_holds_the_exception_after_the_release(
        self,
    ) -> None:
        """ADR-080 point 6, B-153: when the group reaches the caller, the
        idempotency reservation is already released, and the member is the
        exception the handler raised."""
        store = FakeProcessedMessageStore()
        refusal = _Refused("synthetic refusal")
        event = _Evt(data="test")
        bus = EventBus()

        async def failing(event: _Evt) -> None:
            raise refusal

        bus.register(
            _Evt,
            failing,
            behaviors=[EventIdempotencyBehavior(store, consumer="projection")],
        )

        group = await _raised(bus.dispatch(event))

        assert isinstance(group, cqrs_exceptions.EventHandlersFailed)
        assert group.exceptions[0] is refusal
        assert await _is_free(store, _key("projection", event.event_id))

    @pytest.mark.anyio
    async def test_handlers_failed_is_a_cqrs_error_and_an_exception_group(
        self,
    ) -> None:
        handlers_failed = cqrs_exceptions.EventHandlersFailed
        assert issubclass(handlers_failed, CQRSError)
        assert issubclass(handlers_failed, ExceptionGroup)
        assert issubclass(handlers_failed, Exception)
        bus, _ = _failing_bus(ValueError("first"), RuntimeError("second"))
        caught: list[BaseException] = []

        try:
            await bus.dispatch(_Evt(data="test"))
        except* RuntimeError as group:
            caught.extend(group.exceptions)
        except* ValueError as group:
            caught.extend(group.exceptions)

        assert [type(e) for e in caught] == [RuntimeError, ValueError]

    @pytest.mark.anyio
    async def test_handlers_failed_message_names_the_event_type_and_count_only(
        self,
    ) -> None:
        """B-195: a value in a member's message reaches neither ``str()`` nor
        ``repr()`` of the group."""
        bus, _ = _failing_bus(ValueError(f"refused {CONTENT_SENTINEL}"))

        group = await _raised(bus.dispatch(_Evt(data=CONTENT_SENTINEL)))

        assert isinstance(group, cqrs_exceptions.EventHandlersFailed)
        assert group.message == "1 handler(s) failed for _Evt"
        assert group.event_type == "_Evt"
        assert CONTENT_SENTINEL not in str(group)
        assert CONTENT_SENTINEL not in repr(group)
        assert repr(group) == "EventHandlersFailed('1 handler(s) failed for _Evt')"

    @pytest.mark.parametrize(
        "round_trip",
        [
            lambda group: pickle.loads(pickle.dumps(group)),
            copy.copy,
            copy.deepcopy,
        ],
        ids=["pickle", "copy", "deepcopy"],
    )
    @pytest.mark.anyio
    async def test_handlers_failed_survives_pickle_and_copy(
        self, round_trip: Callable[[Any], Any]
    ) -> None:
        bus, _ = _failing_bus(ValueError("first"), RuntimeError("second"))
        group = await _raised(bus.dispatch(_Evt(data="test")))
        assert isinstance(group, cqrs_exceptions.EventHandlersFailed)
        group.add_note("synthetic note")

        restored = round_trip(group)

        assert type(restored) is cqrs_exceptions.EventHandlersFailed
        assert restored.event_type == "_Evt"
        assert restored.message == "2 handler(s) failed for _Evt"
        assert str(restored) == str(group)
        assert restored.__notes__ == ["synthetic note"]
        assert [(type(e), e.args) for e in restored.exceptions] == [
            (ValueError, ("first",)),
            (RuntimeError, ("second",)),
        ]

    @pytest.mark.anyio
    async def test_handlers_failed_split_recounts(self) -> None:
        """``split()`` and ``except*`` keep the type and the notes and recount."""
        bus, _ = _failing_bus(ValueError("first"), RuntimeError("second"))
        group = await _raised(bus.dispatch(_Evt(data="test")))
        assert isinstance(group, cqrs_exceptions.EventHandlersFailed)
        group.add_note("synthetic note")

        match, rest = group.split(ValueError)
        caught: list[BaseException] = []
        try:
            raise group
        except* ValueError as part:
            caught.append(part)
        except* RuntimeError:
            pass

        for part in (match, rest, *caught):
            assert type(part) is cqrs_exceptions.EventHandlersFailed
            assert part.message == "1 handler(s) failed for _Evt"
            assert part.event_type == "_Evt"
            assert part.__notes__ == ["synthetic note"]
        assert match is not None and rest is not None
        assert [type(e) for e in match.exceptions] == [ValueError]
        assert [type(e) for e in rest.exceptions] == [RuntimeError]
        assert [type(e) for e in caught[0].exceptions] == [ValueError]

    @pytest.mark.anyio
    async def test_handlers_failed_split_by_predicate_and_subgroup_recount(
        self,
    ) -> None:
        """(guard) ``split(predicate)`` and ``subgroup(type)`` go through
        ``derive`` too: the type, the notes and a recounted message stay."""
        bus, _ = _failing_bus(
            ValueError("first"), RuntimeError("second"), ValueError("third")
        )
        group = await _raised(bus.dispatch(_Evt(data="test")))
        assert isinstance(group, EventHandlersFailed)
        group.add_note("synthetic note")

        match, rest = group.split(lambda e: str(e) == "second")
        values = group.subgroup(ValueError)

        assert match is not None and rest is not None and values is not None
        for part, count in ((match, 1), (rest, 2), (values, 2)):
            assert type(part) is EventHandlersFailed
            assert part.message == f"{count} handler(s) failed for _Evt"
            assert part.event_type == "_Evt"
            assert part.__notes__ == ["synthetic note"]
        assert [str(e) for e in match.exceptions] == ["second"]
        assert [str(e) for e in rest.exceptions] == ["first", "third"]
        assert [str(e) for e in values.exceptions] == ["first", "third"]

    @pytest.mark.anyio
    async def test_nested_handler_failure_is_a_nested_group(self) -> None:
        """A handler that dispatches directly to a failing handler fails with
        the inner group, which the outer group holds."""
        bus = EventBus()
        refusal = ValueError("inner refusal")

        async def relay(event: _Evt) -> None:
            await bus.dispatch(_OtherEvt(value=1))

        async def failing(event: _OtherEvt) -> None:
            raise refusal

        bus.register(_Evt, relay)
        bus.register(_OtherEvt, failing)

        outer = await _raised(bus.dispatch(_Evt(data="test")))

        assert isinstance(outer, cqrs_exceptions.EventHandlersFailed)
        assert outer.message == "1 handler(s) failed for _Evt"
        (inner,) = outer.exceptions
        assert isinstance(inner, cqrs_exceptions.EventHandlersFailed)
        assert inner.message == "1 handler(s) failed for _OtherEvt"
        assert inner.exceptions == (refusal,)
        reached: list[BaseException] = []
        try:
            raise outer
        except* ValueError as part:
            reached.append(part)
        (part,) = reached
        assert isinstance(part, cqrs_exceptions.EventHandlersFailed)
        (inner_part,) = part.exceptions
        assert isinstance(inner_part, cqrs_exceptions.EventHandlersFailed)
        assert inner_part.exceptions == (refusal,)

    @pytest.mark.anyio
    async def test_handlers_failed_args_are_those_of_an_exception_group(
        self,
    ) -> None:
        """``args`` is ``(message, exceptions)``, as for any ``ExceptionGroup``."""
        first, second = ValueError("first"), RuntimeError("second")
        bus, _ = _failing_bus(first, second)

        group = await _raised(bus.dispatch(_Evt(data="test")))

        assert isinstance(group, EventHandlersFailed)
        message, members = group.args
        assert message == "2 handler(s) failed for _Evt"
        assert list(members) == [first, second]

    @pytest.mark.anyio
    async def test_each_handler_gets_its_own_event_context(self) -> None:
        """Each handler's pipeline sees an EVENT context with the event's
        tracing ids, no unit of work and empty metadata of its own."""
        seen: list[MessageContext] = []

        class Spy:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                seen.append(ctx)
                ctx.metadata["seen"] = True
                return await next()

        async def handler(event: _Evt) -> None:
            pass

        bus = EventBus()
        bus.register(_Evt, handler, behaviors=[Spy()])
        bus.register(_Evt, handler, behaviors=[Spy()])
        event = _Evt(data="test", correlation_id=uuid4(), causation_id=uuid4())

        await bus.dispatch(event)

        assert len(seen) == 2
        for ctx in seen:
            assert ctx.kind is MessageKind.EVENT
            assert ctx.uow is None
            assert ctx.correlation_id == event.correlation_id
            assert ctx.causation_id == event.causation_id
            assert ctx.metadata == {"seen": True}
        assert seen[0].metadata is not seen[1].metadata

    @pytest.mark.anyio
    async def test_failing_callable_instance_is_logged_and_raised(
        self, caplog: Any
    ) -> None:
        """A handler without ``__name__`` is still named in the log line, by
        its type's ``__qualname__`` since DCE-153 (B-201), and its failure is
        a member of the group."""
        refusal = ValueError("synthetic refusal")

        class Projection:
            async def __call__(self, event: _Evt) -> None:
                raise refusal

        bus = EventBus()
        bus.register(_Evt, Projection())
        caplog.set_level(logging.ERROR, logger="pydomain.event_bus")

        group = await _raised(bus.dispatch(_Evt(data="test")))

        assert isinstance(group, EventHandlersFailed)
        assert group.exceptions == (refusal,)
        (line,) = [r.getMessage() for r in caplog.records]
        assert line.split("\n")[0] == (
            f"Event handler {Projection.__qualname__} failed for _Evt: ValueError"
        )

    @pytest.mark.anyio
    async def test_dispatch_many_still_swallows_handler_failures(self) -> None:
        """(guard) The post-command path logs and swallows (ADR-081 point 1)."""
        bus, ran = _failing_bus(ValueError("first"), RuntimeError("second"))

        await bus.dispatch_many([_Evt(data="a"), _Evt(data="b")])

        assert ran == ["fail1", "ok", "fail2"] * 2


# ════════════════════════════════════════════════════════════════════════════
# Exports
# ════════════════════════════════════════════════════════════════════════════
# DCE-154: the handler list is a snapshot per dispatch (ADR-081 point 7, B-187)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("many", [False, True], ids=["dispatch", "dispatch_many"])
@pytest.mark.anyio
async def test_handler_registered_during_dispatch_runs_from_the_next_dispatch(
    many: bool,
) -> None:
    bus = EventBus()
    ran: list[str] = []
    registered: list[bool] = []

    async def late(event: _Evt) -> None:
        ran.append("late")

    async def first(event: _Evt) -> None:
        ran.append("first")
        if not registered:
            registered.append(True)
            bus.register(_Evt, late)

    bus.register(_Evt, first)

    if many:
        await bus.dispatch_many([_Evt(data="a")])
    else:
        await bus.dispatch(_Evt(data="a"))
    assert ran == ["first"]

    ran.clear()
    if many:
        await bus.dispatch_many([_Evt(data="b")])
    else:
        await bus.dispatch(_Evt(data="b"))
    assert ran == ["first", "late"]


# ════════════════════════════════════════════════════════════════════════════


class TestExports:
    """EventBus is properly exported."""

    def test_eventbus_in_cqrs_module(self) -> None:
        """EventBus is importable from pydomain.cqrs."""
        from pydomain.cqrs import EventBus as EB

        assert EB is EventBus

    def test_eventbus_in_top_level(self) -> None:
        """EventBus is importable from pydomain."""
        import pydomain

        assert pydomain.EventBus is EventBus
