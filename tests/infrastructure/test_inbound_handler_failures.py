"""DCE-152 (ADR-081 points 1 to 3; W2-3 phase 1): what the inbound gateway ACKs
and NACKs, end to end through a real ``MessageBus``.

Rows INF-01 and A1; decisions B-180 to B-183, B-199 and B-200. Each test drives
one protocol-conforming subscriber double, ``_AckNackSubscriber``: it ACKs when
the gateway's handler returns and NACKs when it raises an ``Exception``,
recording ``"ack"`` and ``"nack <ExceptionType>"`` as
``InMemoryMessageSubscriber.outcomes`` does. Behind the gateway a handler
failure is always recorded as ``"nack EventHandlersFailed"``.

New names are reached by attribute access (``cqrs_exceptions.EventHandlersFailed``),
so the module collects before they exist. Synthetic shipment data only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import field_validator

from pydomain.cqrs import Command, EmptyCommandResult, Query, QueryResult
from pydomain.cqrs import exceptions as cqrs_exceptions
from pydomain.cqrs.behaviors import EventIdempotencyBehavior
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import FakeProcessedMessageStore, FakeUnitOfWork
from tests.cqrs.test_idempotency_reservation import _outcome, _reached, _settle

# ---------------------------------------------------------------------------
# The subscriber double and the synthetic domain
# ---------------------------------------------------------------------------

_TOPIC = "shipping.shipment.dispatched"


class _AckNackSubscriber:
    """A ``MessageSubscriber`` that ACKs on return and NACKs on an ``Exception``.

    ``errors`` holds what each NACKed delivery raised (``None`` for an ACK). A
    ``BaseException`` that is not an ``Exception`` propagates and records
    nothing, as a broker client sees a cancellation.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}
        self.outcomes: list[str] = []
        self.errors: list[Exception | None] = []

    def subscribe(
        self, topic: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        self.handlers[topic] = handler

    async def deliver(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            await self.handlers[topic](dict(payload))
        except Exception as exc:
            self.outcomes.append(f"nack {type(exc).__name__}")
            self.errors.append(exc)
        else:
            self.outcomes.append("ack")
            self.errors.append(None)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _ShipmentDispatchedIE(IntegrationEvent):
    shipment_id: str


class _ShipmentDispatched(DomainEvent):
    shipment_id: UUID


class _Refused(Exception):
    """A handler refuses the shipment."""


def _translate(event: _ShipmentDispatchedIE) -> _ShipmentDispatched:
    return _ShipmentDispatched(shipment_id=UUID(event.shipment_id))


def _payload(**extra: str) -> dict[str, Any]:
    return {"shipment_id": str(uuid4()), **extra}


_EVENT_ID = "0195e000-0000-7000-8000-0000000000e1"
_CORRELATION_ID = "0195e000-0000-7000-8000-0000000000c1"


def _wire(
    bus: MessageBus,
    translator: Callable[[Any], Any] = _translate,
    integration_class: type[IntegrationEvent] = _ShipmentDispatchedIE,
) -> _AckNackSubscriber:
    subscriber = _AckNackSubscriber()
    gateway = InboundEventGateway(subscriber, bus)
    gateway.register_translation(_TOPIC, integration_class, translator)
    return subscriber


def _translation_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "pydomain.message_subscriber"
        and r.levelno == logging.ERROR
        and r.getMessage().startswith("Translation failed")
    ]


# ---------------------------------------------------------------------------
# Handler failures are NACKed
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-152 INF-01: EventBus swallows the failure, the gateway returns, ACK",
)
@pytest.mark.anyio
async def test_inbound_handler_failure_is_nacked() -> None:
    """The tracer (INF-01): one failing handler, one NACK."""
    bus = MessageBus()
    ran: list[UUID] = []

    async def failing(event: _ShipmentDispatched) -> None:
        ran.append(event.event_id)
        raise _Refused("synthetic refusal")

    bus.register_event(_ShipmentDispatched, failing)
    subscriber = _wire(bus)

    await subscriber.deliver(_TOPIC, _payload())

    assert subscriber.outcomes == ["nack EventHandlersFailed"]
    assert len(ran) == 1
    group = subscriber.errors[0]
    assert isinstance(group, cqrs_exceptions.EventHandlersFailed)
    assert [type(e) for e in group.exceptions] == [_Refused]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-152 INF-01: EventBus swallows the failure, the gateway returns, ACK",
)
@pytest.mark.anyio
async def test_inbound_failure_of_one_handler_runs_the_others_then_nacks() -> None:
    """In-process isolation stays: every handler runs before the NACK."""
    bus = MessageBus()
    ran: list[str] = []

    async def projection(event: _ShipmentDispatched) -> None:
        ran.append("projection")

    async def failing(event: _ShipmentDispatched) -> None:
        ran.append("failing")
        raise _Refused("synthetic refusal")

    async def mailer(event: _ShipmentDispatched) -> None:
        ran.append("mailer")

    bus.register_event(_ShipmentDispatched, projection)
    bus.register_event(_ShipmentDispatched, failing)
    bus.register_event(_ShipmentDispatched, mailer)
    subscriber = _wire(bus)

    await subscriber.deliver(_TOPIC, _payload())

    assert subscriber.outcomes == ["nack EventHandlersFailed"]
    assert ran == ["projection", "failing", "mailer"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-152 A1: research S3, both failed deliveries are ACKed",
)
@pytest.mark.anyio
async def test_inbound_redelivery_of_a_failing_idempotent_handler_is_nacked_each_time() -> (  # noqa: E501
    None
):
    """Research S3 (the W2-2 review probe): the failure is not marked, so the
    redelivery runs the handler again with the same derived ``event_id``, and
    both deliveries are NACKed."""
    bus = MessageBus()
    seen: list[UUID] = []

    async def failing(event: _ShipmentDispatched) -> None:
        seen.append(event.event_id)
        raise _Refused("synthetic refusal")

    bus.register_event(
        _ShipmentDispatched,
        failing,
        behaviors=[
            EventIdempotencyBehavior(FakeProcessedMessageStore(), consumer="s3")
        ],
    )
    subscriber = _wire(bus)
    payload = _payload(event_id=_EVENT_ID)

    await subscriber.deliver(_TOPIC, payload)
    await subscriber.deliver(_TOPIC, payload)

    assert subscriber.outcomes == ["nack EventHandlersFailed"] * 2
    assert len(seen) == 2
    assert seen[0] == seen[1]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-152 A1: research S4, the refused duplicate and the failure are ACKed",
)
@pytest.mark.anyio
async def test_inbound_concurrent_duplicate_is_nacked() -> None:
    """Research S4 (ADR-080 point 6): a duplicate arrives while the first
    delivery runs and is refused with ``EventInProgressError``; the first then
    fails. Both are NACKed, so the event is not lost."""
    bus = MessageBus()
    entered = asyncio.Event()
    gate = asyncio.Event()
    runs: list[UUID] = []

    async def slow(event: _ShipmentDispatched) -> None:
        runs.append(event.event_id)
        entered.set()
        await gate.wait()
        raise _Refused("synthetic refusal")

    bus.register_event(
        _ShipmentDispatched,
        slow,
        behaviors=[
            EventIdempotencyBehavior(FakeProcessedMessageStore(), consumer="s4")
        ],
    )
    subscriber = _wire(bus)
    payload = _payload(event_id=_EVENT_ID)

    first = asyncio.ensure_future(subscriber.deliver(_TOPIC, payload))
    await _reached(entered)
    await subscriber.deliver(_TOPIC, payload)  # the duplicate, refused
    gate.set()
    assert await _outcome(first) is None

    assert subscriber.outcomes == ["nack EventHandlersFailed"] * 2
    assert len(runs) == 1
    duplicate, failure = subscriber.errors
    assert isinstance(duplicate, cqrs_exceptions.EventHandlersFailed)
    assert [type(e) for e in duplicate.exceptions] == [
        cqrs_exceptions.EventInProgressError
    ]
    assert isinstance(failure, cqrs_exceptions.EventHandlersFailed)
    assert [type(e) for e in failure.exceptions] == [_Refused]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-152 B-182: the misuse error is swallowed and the message ACKed",
)
@pytest.mark.anyio
async def test_inbound_idempotency_misuse_is_nacked() -> None:
    """B-182: one behavior instance on two handlers fails every delivery of the
    second; it is NACKed, so the broker's dead-letter queue catches it."""
    bus = MessageBus()
    ran: list[str] = []
    shared = EventIdempotencyBehavior(FakeProcessedMessageStore(), consumer="shared")

    async def projection(event: _ShipmentDispatched) -> None:
        ran.append("projection")

    async def mailer(event: _ShipmentDispatched) -> None:
        ran.append("mailer")

    bus.register_event(_ShipmentDispatched, projection, behaviors=[shared])
    bus.register_event(_ShipmentDispatched, mailer, behaviors=[shared])
    subscriber = _wire(bus)

    await subscriber.deliver(_TOPIC, _payload(event_id=_EVENT_ID))

    assert subscriber.outcomes == ["nack EventHandlersFailed"]
    assert ran == ["projection"]
    group = subscriber.errors[0]
    assert isinstance(group, cqrs_exceptions.EventHandlersFailed)
    assert [type(e) for e in group.exceptions] == [
        cqrs_exceptions.EventIdempotencyMisuseError
    ]


# ---------------------------------------------------------------------------
# Poison messages and messages without handlers are ACKed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inbound_translator_failure_is_acked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(guard) B-183: a translator error is poison: one L2 line, ACK."""
    bus = MessageBus()
    ran: list[str] = []

    async def handler(event: _ShipmentDispatched) -> None:
        ran.append("handler")

    def failing_translator(event: _ShipmentDispatchedIE) -> _ShipmentDispatched:
        raise ValueError("synthetic translation failure")

    bus.register_event(_ShipmentDispatched, handler)
    subscriber = _wire(bus, failing_translator)
    caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

    await subscriber.deliver(_TOPIC, _payload())

    assert subscriber.outcomes == ["ack"]
    assert ran == []
    assert len(_translation_failures(caplog)) == 1


@pytest.mark.anyio
async def test_inbound_validation_failure_is_acked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(guard) ADR-060, ADR-071: a payload that fails validation is poison."""
    bus = MessageBus()
    ran: list[str] = []

    async def handler(event: _ShipmentDispatched) -> None:
        ran.append("handler")

    bus.register_event(_ShipmentDispatched, handler)
    subscriber = _wire(bus)
    caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

    await subscriber.deliver(_TOPIC, {"carrier": "synthetic"})

    assert subscriber.outcomes == ["ack"]
    assert ran == []
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage().startswith("Failed to validate payload")


class _StrictShipmentIE(IntegrationEvent):
    """Its validator raises ``TypeError``, which Pydantic does not wrap."""

    shipment_id: str

    @field_validator("shipment_id")
    @classmethod
    def _refuse(cls, value: str) -> str:
        raise TypeError("synthetic validator failure")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-152 B-200: a TypeError from validation escapes unlogged and is NACKed",
)
@pytest.mark.anyio
async def test_inbound_non_validation_error_from_validation_is_acked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """B-200: any other ``Exception`` from validation is a translation failure:
    one L2 line, ACK (the payload will never pass)."""
    bus = MessageBus()
    ran: list[str] = []

    async def handler(event: _ShipmentDispatched) -> None:
        ran.append("handler")

    bus.register_event(_ShipmentDispatched, handler)
    subscriber = _wire(bus, integration_class=_StrictShipmentIE)
    caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

    await subscriber.deliver(_TOPIC, _payload())

    assert subscriber.outcomes == ["ack"]
    assert ran == []
    assert len(_translation_failures(caplog)) == 1


class _CancelShipment(Command[EmptyCommandResult]):
    shipment_id: UUID


class _ShipmentStatus(QueryResult):
    status: str


class _GetShipment(Query[_ShipmentStatus]):
    shipment_id: UUID


class _Parcel:
    """A plain object, neither a message nor an event."""

    def __init__(self, shipment_id: str) -> None:
        self.shipment_id = shipment_id


def _to_command(event: _ShipmentDispatchedIE) -> Any:
    return _CancelShipment(shipment_id=UUID(event.shipment_id))


def _to_query(event: _ShipmentDispatchedIE) -> Any:
    return _GetShipment(shipment_id=UUID(event.shipment_id))


def _to_object(event: _ShipmentDispatchedIE) -> Any:
    return _Parcel(event.shipment_id)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "DCE-152 B-199: a non-DomainEvent is dispatched, raises an unlogged "
        "AttributeError or is NACKed"
    ),
)
@pytest.mark.parametrize(
    "translator",
    [_to_command, _to_query, _to_object],
    ids=["command", "query", "plain-object"],
)
@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"event_id": _EVENT_ID},
        {"correlation_id": _CORRELATION_ID},
        {"event_id": _EVENT_ID, "correlation_id": _CORRELATION_ID},
    ],
    ids=["no-ids", "event-id", "correlation-id", "both-ids"],
)
@pytest.mark.anyio
async def test_inbound_translator_returning_a_non_domain_event_is_acked(
    translator: Callable[[Any], Any],
    extra: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """B-199: the result is checked right after the translator returns, before
    the ``event_id`` derivation: one L2 line, ACK, and no command or query
    handler runs."""
    bus = MessageBus()
    ran: list[str] = []

    async def cancel(command: _CancelShipment, uow: Any = None) -> EmptyCommandResult:
        ran.append("command")
        return EmptyCommandResult()

    async def get(query: _GetShipment) -> _ShipmentStatus:
        ran.append("query")
        return _ShipmentStatus(status="synthetic")

    bus.register_command(_CancelShipment, cancel, uow_factory=FakeUnitOfWork)
    bus.register_query(_GetShipment, get)
    subscriber = _wire(bus, translator)
    caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

    await subscriber.deliver(_TOPIC, _payload(**extra))

    assert subscriber.outcomes == ["ack"]
    assert ran == []
    assert len(_translation_failures(caplog)) == 1


@pytest.mark.anyio
async def test_inbound_event_without_handlers_is_acked() -> None:
    """(guard) No handler is registered: nothing runs, ACK."""
    subscriber = _wire(MessageBus())

    await subscriber.deliver(_TOPIC, _payload())

    assert subscriber.outcomes == ["ack"]


@pytest.mark.anyio
async def test_inbound_cancelled_handler_propagates_the_cancellation() -> None:
    """(guard) A cancelled handler propagates at once: no outcome is recorded
    and the later handler does not run."""
    bus = MessageBus()
    entered = asyncio.Event()
    ran: list[str] = []

    async def stuck(event: _ShipmentDispatched) -> None:
        ran.append("stuck")
        entered.set()
        await asyncio.Event().wait()

    async def later(event: _ShipmentDispatched) -> None:
        ran.append("later")

    bus.register_event(_ShipmentDispatched, stuck)
    bus.register_event(_ShipmentDispatched, later)
    subscriber = _wire(bus)

    delivery = asyncio.ensure_future(subscriber.deliver(_TOPIC, _payload()))
    await _reached(entered)
    delivery.cancel()
    await _settle()

    assert isinstance(await _outcome(delivery), asyncio.CancelledError)
    assert subscriber.outcomes == []
    assert ran == ["stuck"]
