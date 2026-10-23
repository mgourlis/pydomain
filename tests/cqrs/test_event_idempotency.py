"""DCE-150 (ADR-080 points 3, 4, 6 and 7; W2-2 phase 3): an event is keyed on
its ``event_id`` and its consumer, and marked after its handler succeeds.
Rows CQRS-05, CQRS-K2, A31; decisions B-147 to B-150, B-152, B-153, B-157 (a),
(b) and (f), B-159, B-161, B-164, B-165, B-167.

Every dispatch that a test cancels runs in its own task, and the test reads
its outcome with ``_outcome``, so no ``CancelledError`` reaches the test's
own task. Synthetic shipment data only.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import warnings
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

from pydomain.cqrs import Command, CommandResult
from pydomain.cqrs import exceptions as cqrs_exceptions
from pydomain.cqrs.behaviors import (
    EventIdempotencyBehavior,
    IdempotencyBehavior,
    MessageContext,
    MessageKind,
    MessagePipeline,
)
from pydomain.cqrs.event_bus import EventBus
from pydomain.cqrs.exceptions import CQRSError
from pydomain.cqrs.idempotency import MISSING
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import (
    FakeProcessedMessageStore,
    FakeRepository,
    FakeUnitOfWork,
    InMemoryMessageSubscriber,
)
from tests.cqrs.test_idempotency_reservation import (
    _TTL,
    _drive_until_parked,
    _FailingRelease,
    _GatedReserveStore,
    _is_free,
    _outcome,
    _park,
    _reached,
    _settle,
)
from tests.support.content_sentinel import (
    CONTENT_SENTINEL,
    assert_absent_from_logs,
    assert_absent_from_reprs,
)

# ---------------------------------------------------------------------------
# Synthetic domain and helpers
# ---------------------------------------------------------------------------

_KEY_NAMESPACE = UUID("6122d529-5666-59f9-aeb0-cda93ddbb124")
"""The documented value of the key namespace (ADR-080 point 3)."""


def _key(consumer: str, event_id: UUID) -> UUID:
    """The dedup key ADR-080 point 3 defines."""
    return uuid5(uuid5(_KEY_NAMESPACE, consumer), str(event_id))


class _Shipped(DomainEvent):
    shipment_id: UUID
    note: str = ""


def _shipped(note: str = "") -> _Shipped:
    """A shipment event with a ``causation_id``, as a stamped event has."""
    return _Shipped(shipment_id=uuid4(), note=note, causation_id=uuid4())


class _Refused(Exception):
    """The handler refuses the shipment."""


def _idempotent(
    store: Any, consumer: str = "shipping-projection"
) -> EventIdempotencyBehavior:
    """The behavior under test, named for its consumer."""
    return EventIdempotencyBehavior(store, consumer=consumer)


class _Run:
    """One handler run's script: a gate to wait on and whether to refuse."""

    def __init__(self, *, gated: bool = False, refuse: bool = False) -> None:
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        if not gated:
            self.gate.set()
        self.refuse = refuse


class _Harness:
    """One event handler behind one ``EventIdempotencyBehavior``.

    ``deliver`` runs the pipeline directly, so the behavior's own errors reach
    the test (``EventBus`` would log and swallow them).
    """

    consumer = "shipping-projection"

    def __init__(self, store: Any, *, runs: list[_Run] | None = None) -> None:
        self.store = store
        self.script = list(runs or [])
        self.runs = 0
        self.pipeline = MessagePipeline(
            self.handle, [_idempotent(store, self.consumer)]
        )

    async def handle(self, event: _Shipped) -> None:
        self.runs += 1
        run = self.script.pop(0) if self.script else _Run()
        run.entered.set()
        await asyncio.sleep(0)  # a handler awaits, as a real one does
        await run.gate.wait()
        if run.refuse:
            raise _Refused("synthetic refusal")

    async def deliver(self, event: _Shipped) -> None:
        ctx = MessageContext(message=event, kind=MessageKind.EVENT)
        await self.pipeline.execute(ctx, event)

    def start(self, event: _Shipped) -> asyncio.Task[Any]:
        return asyncio.ensure_future(self.deliver(event))

    def key(self, event: _Shipped) -> UUID:
        return _key(self.consumer, event.event_id)


def _in_progress(error: BaseException | None) -> bool:
    """Whether *error* is an ``EventInProgressError``."""
    assert error is not None, "the duplicate was not refused"
    return isinstance(error, cqrs_exceptions.EventInProgressError)


class _FailingSet(FakeProcessedMessageStore):
    """``set`` raises after the handler succeeded."""

    async def set(self, message_id: UUID, result: Any) -> None:
        raise RuntimeError(CONTENT_SENTINEL)


class _MarksOnly:
    """A ``ProcessedMessageStore`` without ``MessageReservationStore``.

    Records every call in *calls*, which the handler shares.
    """

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.marks: set[UUID] = set()

    async def get(self, message_id: UUID) -> Any:
        raise AssertionError("not used by EventIdempotencyBehavior")

    async def set(self, message_id: UUID, result: Any) -> None:
        raise AssertionError("not used by EventIdempotencyBehavior")

    async def contains(self, message_id: UUID) -> bool:
        self.calls.append("contains")
        return message_id in self.marks

    async def check_and_set(self, message_id: UUID) -> bool:
        self.calls.append("check_and_set")
        if message_id in self.marks:
            return True
        self.marks.add(message_id)
        return False


def _pipeline_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "pydomain.pipeline"]


# A synthetic command whose handler raises one or more shipment events.


class _Shipment(AggregateRoot[UUID]):
    def ship(self) -> None:
        self._add_event(_Shipped(shipment_id=self.id))


class _ShipResult(CommandResult):
    pass


class _Ship(Command[_ShipResult]):
    shipments: int = 1


def _shipping_bus(
    *, command_store: FakeProcessedMessageStore | None = None
) -> MessageBus:
    """A bus whose ``_Ship`` command raises one ``_Shipped`` per shipment."""
    repo: FakeRepository[_Shipment, UUID] = FakeRepository()

    async def ship(command: _Ship, uow: Any) -> _ShipResult:
        for _ in range(command.shipments):
            shipment = _Shipment(id=uuid4())
            shipment.ship()
            await repo.save(shipment)
        return _ShipResult()

    bus = MessageBus()
    bus.register_command(
        _Ship,
        ship,
        lambda: FakeUnitOfWork(repository=repo),
        behaviors=[] if command_store is None else [IdempotencyBehavior(command_store)],
    )
    return bus


# ---------------------------------------------------------------------------
# Sibling events and several handlers (CQRS-05)
# ---------------------------------------------------------------------------


class TestSiblingEventsAndHandlers:
    @pytest.mark.anyio
    async def test_two_handlers_sharing_a_store_both_run(self) -> None:
        """``cqrs/p_04_09.py`` CQRS-05 (b): ``ran=['projection', 'mailer']``."""
        bus = _shipping_bus()
        store = FakeProcessedMessageStore()
        ran: list[str] = []

        async def projection(event: _Shipped) -> None:
            ran.append("projection")

        async def mailer(event: _Shipped) -> None:
            ran.append("mailer")

        bus.register_event(
            _Shipped, projection, behaviors=[_idempotent(store, "projection")]
        )
        bus.register_event(_Shipped, mailer, behaviors=[_idempotent(store, "mailer")])
        await bus.dispatch(_Ship())

        assert ran == ["projection", "mailer"]

    @pytest.mark.anyio
    async def test_dispatching_one_event_object_twice_skips_the_second(
        self,
    ) -> None:
        """An event with ``causation_id = None`` is keyed on its ``event_id``
        too (B-161 (b)): it used to pass unchecked."""
        bus = MessageBus()
        ran: list[UUID] = []

        async def projection(event: _Shipped) -> None:
            ran.append(event.event_id)

        bus.register_event(
            _Shipped, projection, behaviors=[_idempotent(FakeProcessedMessageStore())]
        )
        event = _Shipped(shipment_id=uuid4())
        assert event.causation_id is None

        await bus.dispatch(event)
        await bus.dispatch(event)

        assert ran == [event.event_id]


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


class TestDuplicates:
    @pytest.mark.anyio
    async def test_duplicate_after_success_is_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The INFO line names the event type and its ``event_id``."""
        caplog.set_level(logging.INFO, logger="pydomain.pipeline")
        harness = _Harness(FakeProcessedMessageStore())
        event = _shipped(note=CONTENT_SENTINEL)

        await harness.deliver(event)
        await harness.deliver(event)

        assert harness.runs == 1
        assert _pipeline_records(caplog) == [
            f"Skipping duplicate event _Shipped (event_id={event.event_id})"
        ]
        assert_absent_from_logs(caplog)

    @pytest.mark.anyio
    async def test_duplicate_after_success_is_skipped_before_reserving(
        self,
    ) -> None:
        """ADR-080 point 4, step 1: the ``get`` hit skips; nothing is reserved."""

        class CountsReserves(FakeProcessedMessageStore):
            reserves = 0

            async def reserve(self, message_id: UUID) -> Any:
                self.reserves += 1
                return await super().reserve(message_id)

        store = CountsReserves()
        harness = _Harness(store)
        event = _shipped()

        await harness.deliver(event)
        await harness.deliver(event)

        assert harness.runs == 1
        assert store.reserves == 1

    @pytest.mark.anyio
    async def test_concurrent_duplicate_raises_event_in_progress(self) -> None:
        run = _Run(gated=True)
        harness = _Harness(FakeProcessedMessageStore(), runs=[run])
        event = _shipped()
        first = harness.start(event)
        await _reached(run.entered)

        error = await _outcome(harness.deliver(event))
        run.gate.set()
        first_error = await _outcome(first)

        assert first_error is None
        assert _in_progress(error)
        assert isinstance(error, CQRSError)
        assert harness.runs == 1
        # The first delivery completed: a redelivery now is skipped.
        await harness.deliver(event)
        assert harness.runs == 1

    @pytest.mark.anyio
    async def test_event_in_progress_error_names_the_event_type_only(
        self,
    ) -> None:
        """ADR-071: no content, no id."""
        run = _Run(gated=True)
        harness = _Harness(FakeProcessedMessageStore(), runs=[run])
        event = _shipped(note=CONTENT_SENTINEL)
        first = harness.start(event)
        await _reached(run.entered)

        error = await _outcome(harness.deliver(event))
        run.gate.set()
        await _outcome(first)

        assert _in_progress(error)
        assert str(error) == "Event _Shipped is already in progress"
        assert str(event.event_id) not in str(error)
        assert str(event.shipment_id) not in str(error)
        assert_absent_from_reprs(error)

    @pytest.mark.anyio
    async def test_duplicate_that_completed_meanwhile_is_skipped(self) -> None:
        """``reserve`` returns ``None`` because the first delivery completed
        between the duplicate's ``get`` and its ``reserve``: the second
        ``get`` finds the mark, and the duplicate is skipped (B-157 (b))."""

        class MissesTheFirstGet(FakeProcessedMessageStore):
            gets = 0

            async def get(self, message_id: UUID) -> Any:
                self.gets += 1
                return MISSING if self.gets == 1 else await super().get(message_id)

        store = MissesTheFirstGet()
        harness = _Harness(store)
        event = _shipped()
        assert await store.reserve(harness.key(event)) is not None
        await store.set(harness.key(event), True)

        error = await _outcome(harness.deliver(event))

        assert error is None
        assert harness.runs == 0
        assert store.gets == 2


# ---------------------------------------------------------------------------
# Failures and cancellation (CQRS-K2)
# ---------------------------------------------------------------------------


class TestFailuresAndCancellation:
    @pytest.mark.anyio
    async def test_failed_handler_runs_again_on_redelivery(self) -> None:
        """The handler's own error leaves the pipeline unchanged, after the
        release (ADR-080 point 6); the redelivery runs the handler again."""
        store = FakeProcessedMessageStore()
        harness = _Harness(store, runs=[_Run(refuse=True)])
        event = _shipped()

        error = await _outcome(harness.deliver(event))
        free = await _is_free(store, harness.key(event))
        again = await _outcome(harness.deliver(event))

        assert isinstance(error, _Refused)
        assert getattr(error, "__notes__", []) == []
        assert again is None
        assert harness.runs == 2
        assert free

    @pytest.mark.anyio
    async def test_cancelled_handler_releases_its_reservation(self) -> None:
        store = FakeProcessedMessageStore()
        run = _Run(gated=True)
        harness = _Harness(store, runs=[run])
        event = _shipped()
        task = harness.start(event)
        await _reached(run.entered)

        task.cancel()
        error = await _outcome(task)
        free = await _is_free(store, harness.key(event))
        again = await _outcome(harness.deliver(event))

        assert isinstance(error, asyncio.CancelledError)
        assert again is None
        assert harness.runs == 2
        assert free

    @pytest.mark.anyio
    async def test_cancel_during_reserve_releases_the_new_token(self) -> None:
        """``reserve`` completes under the shield; its token is released at
        once, the cancellation propagates, and a redelivery runs (B-125)."""
        store = _GatedReserveStore()
        harness = _Harness(store)
        event = _shipped()
        task = harness.start(event)
        await _reached(store.entered)

        task.cancel()
        await _settle()
        store.gate.set()
        error = await _outcome(task)
        again = await _outcome(harness.deliver(event))

        assert isinstance(error, asyncio.CancelledError)
        assert store.tokens[0] is not None
        assert store.released == [store.tokens[0]]
        assert again is None
        assert harness.runs == 1

    @pytest.mark.anyio
    async def test_closed_suspended_dispatch_awaits_nothing(self) -> None:
        """A delivery closed while parked in its handler gets ``GeneratorExit``
        and awaits nothing: its reservation stays until the TTL, unmarked."""
        store = FakeProcessedMessageStore()

        async def parks(event: _Shipped) -> None:
            await _park()

        pipeline = MessagePipeline(parks, [_idempotent(store, "parking")])
        event = _shipped()
        delivering = pipeline.execute(
            MessageContext(message=event, kind=MessageKind.EVENT), event
        )
        await _drive_until_parked(delivering)

        delivering.close()

        assert not await _is_free(store, _key("parking", event.event_id))
        assert await store.get(_key("parking", event.event_id)) is MISSING

    @pytest.mark.anyio
    async def test_failed_release_is_a_type_only_note(self) -> None:
        """A failing ``release`` is a type-only note on the handler's error;
        the key stays reserved until the TTL."""
        store = _FailingRelease(reservation_ttl=_TTL)
        harness = _Harness(store, runs=[_Run(refuse=True)])
        event = _shipped()

        error = await _outcome(harness.deliver(event))
        blocked = await _outcome(harness.deliver(event))
        await asyncio.sleep(_TTL * 2)
        again = await _outcome(harness.deliver(event))

        assert isinstance(error, _Refused)
        notes = getattr(error, "__notes__", [])
        assert notes == ["clean-up also failed: RuntimeError"]
        assert "synthetic-release-detail" not in " ".join(notes)
        assert _in_progress(blocked)
        assert again is None
        assert harness.runs == 2

    @pytest.mark.anyio
    async def test_failed_set_is_a_type_only_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """B-164: the delivery returns normally with a type-only WARNING; the
        reservation stays until its TTL, then a redelivery runs again."""
        caplog.set_level(logging.WARNING, logger="pydomain.pipeline")
        store = _FailingSet(reservation_ttl=_TTL)
        harness = _Harness(store)
        event = _shipped()

        error = await _outcome(harness.deliver(event))
        duplicate = await _outcome(harness.deliver(event))
        await asyncio.sleep(_TTL * 2)
        again = await _outcome(harness.deliver(event))

        assert (
            _pipeline_records(caplog)
            == ["Idempotency record failed for _Shipped: RuntimeError"] * 2
        )
        assert error is None
        assert _in_progress(duplicate)
        assert again is None
        assert harness.runs == 2
        assert_absent_from_logs(caplog)


# ---------------------------------------------------------------------------
# Stores without reservations (ADR-080 point 4, B-157 (a), B-165)
# ---------------------------------------------------------------------------


class _MarksHarness:
    """A handler behind the behavior, on a store without reservations."""

    def __init__(self, store_type: type[_MarksOnly] = _MarksOnly) -> None:
        self.calls: list[str] = []
        self.store = store_type(self.calls)
        self.refuse = False
        self.pipeline = MessagePipeline(self.handle, [_idempotent(self.store)])

    async def handle(self, event: _Shipped) -> None:
        self.calls.append("handler")
        if self.refuse:
            raise _Refused("synthetic refusal")

    async def deliver(self, event: _Shipped) -> None:
        ctx = MessageContext(message=event, kind=MessageKind.EVENT)
        await self.pipeline.execute(ctx, event)


class TestStoresWithoutReservations:
    @pytest.mark.anyio
    async def test_store_without_reservations_marks_after_success(self) -> None:
        """``contains``, the handler, then ``check_and_set``; a failed handler
        is not marked, and a delivery after a success is skipped."""
        harness = _MarksHarness()
        event = _shipped()

        harness.refuse = True
        error = await _outcome(harness.deliver(event))
        harness.refuse = False
        await harness.deliver(event)
        await harness.deliver(event)

        assert isinstance(error, _Refused)
        assert harness.calls == [
            "contains",
            "handler",
            "contains",
            "handler",
            "check_and_set",
            "contains",
        ]
        assert harness.store.marks == {_key("shipping-projection", event.event_id)}

    @pytest.mark.anyio
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-150 B-174: the fallback check_and_set is not shielded",
    )
    async def test_cancelled_fallback_mark_still_completes(self) -> None:
        """B-174: a cancellation that arrives while ``check_and_set`` runs
        after a successful handler waits for the mark, then propagates; a
        redelivery is skipped."""

        class GatedMark(_MarksOnly):
            def __init__(self, calls: list[str]) -> None:
                super().__init__(calls)
                self.entered = asyncio.Event()
                self.gate = asyncio.Event()

            async def check_and_set(self, message_id: UUID) -> bool:
                self.entered.set()
                await self.gate.wait()
                return await super().check_and_set(message_id)

        harness = _MarksHarness(GatedMark)
        store = harness.store
        assert isinstance(store, GatedMark)
        event = _shipped()
        task = asyncio.ensure_future(harness.deliver(event))
        await _reached(store.entered)

        task.cancel()
        await _settle()
        store.gate.set()
        error = await _outcome(task)
        marked = set(store.marks)
        await harness.deliver(event)

        assert isinstance(error, asyncio.CancelledError)
        assert marked == {_key("shipping-projection", event.event_id)}
        assert harness.calls.count("handler") == 1

    @pytest.mark.anyio
    async def test_failed_fallback_mark_is_a_type_only_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """B-165: the delivery returns normally, the event stays unmarked, and
        a redelivery runs the handler again."""

        class FailingMark(_MarksOnly):
            async def check_and_set(self, message_id: UUID) -> bool:
                self.calls.append("check_and_set")
                raise RuntimeError(CONTENT_SENTINEL)

        caplog.set_level(logging.WARNING, logger="pydomain.pipeline")
        harness = _MarksHarness(FailingMark)
        event = _shipped()

        error = await _outcome(harness.deliver(event))
        again = await _outcome(harness.deliver(event))

        assert error is None
        assert again is None
        assert harness.calls.count("handler") == 2
        assert (
            _pipeline_records(caplog)
            == ["Idempotency record failed for _Shipped: RuntimeError"] * 2
        )
        assert_absent_from_logs(caplog)


# ---------------------------------------------------------------------------
# Keys and consumers (ADR-080 point 3)
# ---------------------------------------------------------------------------


async def _on_shipped(event: _Shipped) -> None:
    """A module-level handler, named ``<module>:_on_shipped`` by the fallback."""


class _CallableHandler:
    async def __call__(self, event: _Shipped) -> None:
        pass


async def _deliver_through(behavior: EventIdempotencyBehavior, handler: Any) -> UUID:
    """Deliver one new event to *handler* behind *behavior*; return its id."""
    event = _shipped()
    pipeline = MessagePipeline(handler, [behavior])
    await pipeline.execute(MessageContext(message=event, kind=MessageKind.EVENT), event)
    return event.event_id


class TestKeysAndConsumers:
    @pytest.mark.anyio
    async def test_key_is_uuid5_of_consumer_and_event_id(self) -> None:
        store = FakeProcessedMessageStore()
        behavior = EventIdempotencyBehavior(store, consumer="shipping-projection")

        event_id = await _deliver_through(behavior, _on_shipped)

        assert _KEY_NAMESPACE == uuid5(
            NAMESPACE_URL, "https://github.com/mgourlis/pydomain/event-idempotency-key"
        )
        assert await store.get(_key("shipping-projection", event_id)) is True
        assert await store.get(event_id) is MISSING

    @pytest.mark.anyio
    async def test_missing_consumer_warns_once_at_construction_and_keys_on_the_handler_name(  # noqa: E501
        self,
    ) -> None:
        """B-157 (f): one ``DeprecationWarning``, at construction, pointing at
        the caller; the key uses the handler's ``module:qualname``."""
        store = FakeProcessedMessageStore()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            behavior = EventIdempotencyBehavior(store)
            at_construction = len(caught)
            first = await _deliver_through(behavior, _on_shipped)
            second = await _deliver_through(behavior, _on_shipped)

        assert at_construction == 1
        assert [w.category for w in caught] == [DeprecationWarning]
        assert str(caught[0].message) == (
            "EventIdempotencyBehavior without consumer= keys events on the "
            "handler's module and qualified name; pass consumer=, which "
            "pydomain 0.5.0 requires"
        )
        # It points at the caller; under mutmut, its trampoline is the caller.
        assert caught[0].filename == __file__ or "mutmut" in caught[0].filename
        name = f"{__name__}:_on_shipped"
        assert await store.get(_key(name, first)) is True
        assert await store.get(_key(name, second)) is True

    @pytest.mark.parametrize(
        ("handler", "name"),
        [
            (_CallableHandler(), f"{__name__}:_CallableHandler"),
            (functools.partial(_on_shipped), "functools:partial"),
        ],
        ids=["callable-instance", "partial"],
    )
    @pytest.mark.anyio
    async def test_missing_consumer_names_an_instance_by_its_type(
        self, handler: Any, name: str
    ) -> None:
        """ADR-080 point 3: a callable instance's type gives the name."""
        store = FakeProcessedMessageStore()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            behavior = EventIdempotencyBehavior(store)

        event_id = await _deliver_through(behavior, handler)

        assert await store.get(_key(name, event_id)) is True

    @pytest.mark.anyio
    async def test_one_instance_on_two_handlers_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """B-159, B-167: the first handler runs, the second does not, and
        ``EventBus`` logs one ERROR line with no content (until W2-3)."""
        caplog.set_level(logging.INFO)
        store = FakeProcessedMessageStore()
        shared = _idempotent(store, "shipping")
        ran: list[str] = []

        async def projection(event: _Shipped) -> None:
            ran.append("projection")

        async def mailer(event: _Shipped) -> None:
            ran.append("mailer")

        bus = EventBus()
        bus.register(_Shipped, projection, behaviors=[shared])
        bus.register(_Shipped, mailer, behaviors=[shared])
        await bus.dispatch(_shipped(note=CONTENT_SENTINEL))

        assert ran == ["projection"]
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        assert errors[0].name == "pydomain.event_bus"
        assert errors[0].exc_info is not None
        misuse = errors[0].exc_info[1]
        assert isinstance(misuse, cqrs_exceptions.EventIdempotencyMisuseError)
        assert isinstance(misuse, CQRSError)
        assert str(misuse) == (
            "One EventIdempotencyBehavior instance serves one handler; "
            "build an instance for each registration"
        )
        assert_absent_from_logs(caplog)
        assert_absent_from_reprs(misuse)

    @pytest.mark.anyio
    async def test_one_function_registered_twice_is_one_handler(self) -> None:
        """The limit of B-159: handlers compare with ``==``, so the same
        function in two registrations is one handler, and both share a key."""
        store = FakeProcessedMessageStore()
        shared = _idempotent(store, "shipping")
        ran: list[UUID] = []

        async def projection(event: _Shipped) -> None:
            ran.append(event.event_id)

        bus = EventBus()
        bus.register(_Shipped, projection, behaviors=[shared])
        bus.register(_Shipped, projection, behaviors=[shared])
        event = _shipped()
        await bus.dispatch(event)

        assert ran == [event.event_id]

    @pytest.mark.anyio
    async def test_a_message_that_is_not_a_domain_event_passes_through(
        self,
    ) -> None:
        """No ``event_id``, no key: the store is not consulted."""
        harness = _MarksHarness()
        message = {"shipment": "synthetic"}

        async def handler(message: Any) -> str:
            harness.calls.append("handler")
            return "handled"

        pipeline = MessagePipeline(handler, [_idempotent(harness.store)])
        ctx = MessageContext(message=message, kind=MessageKind.EVENT)

        assert await pipeline.execute(ctx, message) == "handled"
        assert await pipeline.execute(ctx, message) == "handled"
        assert harness.calls == ["handler", "handler"]

    def test_event_idempotency_errors_are_cqrs_errors(self) -> None:
        assert issubclass(cqrs_exceptions.EventInProgressError, CQRSError)
        assert issubclass(cqrs_exceptions.EventIdempotencyMisuseError, CQRSError)


# ---------------------------------------------------------------------------
# End to end through the inbound gateway
# ---------------------------------------------------------------------------


class _ShipmentDispatched(IntegrationEvent):
    shipment_id: str


@pytest.mark.anyio
async def test_inbound_redelivery_after_a_failure_runs_again() -> None:
    """A subscriber redelivers the same message after the handler failed: the
    translator does not copy ``event_id``, so the gateway's derived id (Phase
    2) keys both deliveries alike; the failure was not marked, so the
    redelivery runs, and a third delivery after the success is skipped."""
    bus = MessageBus()
    subscriber = InMemoryMessageSubscriber()
    gateway = InboundEventGateway(subscriber, bus)
    seen: list[UUID] = []

    def translate(event: _ShipmentDispatched) -> _Shipped:
        return _Shipped(shipment_id=UUID(event.shipment_id))

    async def flaky(event: _Shipped) -> None:
        seen.append(event.event_id)
        if len(seen) == 1:
            raise _Refused("synthetic transient failure")

    bus.register_event(
        _Shipped, flaky, behaviors=[_idempotent(FakeProcessedMessageStore())]
    )
    gateway.register_translation("shipments.dispatched", _ShipmentDispatched, translate)
    payload = {
        "shipment_id": str(uuid4()),
        "event_id": "0195e000-0000-7000-8000-0000000000e1",
        "correlation_id": "0195e000-0000-7000-8000-0000000000c1",
    }

    for _ in range(3):
        await subscriber.simulate_message("shipments.dispatched", dict(payload))

    assert len(seen) == 2
    assert seen[0] == seen[1]
