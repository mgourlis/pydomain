"""Payload values never reach library-built errors, logs or reprs (ADR-071).

The cross-cutting content check of the 0.3.0 audit (audit plan §4.6, rows
INF-10 and INF-15): :data:`CONTENT_SENTINEL` goes into a payload, the library
fails on it, and the sentinel must be absent from what the library stored,
logged, raised or printed. The paths span the outbox relay, the inbound
gateway, saga hydration, upcasting and three payload-carrying models, so the
checks live here rather than in one module's folder.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import uuid4

import pytest

from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.cqrs.saga.hydration import hydrate_command
from pydomain.cqrs.saga.state import CompensationRecord
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.exceptions import UpcastError
from pydomain.es.upcasting import EventUpcaster
from pydomain.infrastructure.event_registry import GenericDomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import (
    FakeOutboxStore,
    InMemoryMessageBroker,
    InMemoryMessageSubscriber,
)
from tests.support.content_sentinel import (
    CONTENT_SENTINEL,
    assert_absent_from_logs,
    assert_absent_from_reprs,
    assert_absent_from_strings,
)

# ── Synthetic types ─────────────────────────────────────────────────────


class InvoiceIssued(DomainEvent):
    invoice_id: str
    amount: int


class InvoiceIssuedIntegration(IntegrationEvent):
    invoice_id: str
    amount: int


class ChargeInvoice(Command[EmptyCommandResult]):
    """A saga command that :func:`hydrate_command` resolves from this module."""

    invoice_id: str
    amount: int


class InvoiceV1ToV2(EventUpcaster):
    """An upcaster whose transform fails on a non-numeric amount."""

    source_type: ClassVar[str] = "InvoiceIssued"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        return {**event, "amount_cents": int(event["amount"]) * 100}


def _outbound_registry() -> OutboundEventRegistry:
    registry = OutboundEventRegistry()
    registry.register_translation(
        InvoiceIssued,
        InvoiceIssuedIntegration,
        "invoices.issued",
        lambda event: InvoiceIssuedIntegration(invoice_id="i1", amount=1),
    )
    return registry


def _to_domain(event: InvoiceIssuedIntegration) -> DomainEvent:
    return InvoiceIssued(invoice_id=event.invoice_id, amount=event.amount)


# ── INF-10: stored errors and logs ─────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-118 INF-10: the relay stores and logs input values",
)
@pytest.mark.anyio
async def test_sentinel_absent_from_relay_stored_errors_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing payload leaves no value in mark_failed, the dead letter or the log."""
    caplog.set_level(logging.DEBUG)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = FakeOutboxStore(clock=lambda: now)
    await store.append(
        [
            OutboxEntry(
                message_id="m1",
                topic="invoices.issued",
                payload={"invoice_id": CONTENT_SENTINEL, "amount": CONTENT_SENTINEL},
                occurred_at=now,
            )
        ]
    )
    gateway = OutboundEventGateway(
        store,
        InMemoryMessageBroker(),
        _outbound_registry(),
        base_delay=timedelta(seconds=1),
        max_attempts=2,
        clock=lambda: now,
    )

    await gateway.run_once()  # first failure: mark_failed
    now += timedelta(minutes=1)
    await gateway.run_once()  # budget exhausted: dead-lettered, logged at ERROR

    assert [message_id for message_id, _, _ in store.failures] == ["m1"]
    assert list(store.dead_lettered) == ["m1"]
    assert_absent_from_strings(error for _, error, _ in store.failures)
    assert_absent_from_strings(store.dead_lettered.values())
    assert_absent_from_logs(caplog)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-118 INF-10: inbound validation logs a traceback with values",
)
@pytest.mark.anyio
async def test_sentinel_absent_from_inbound_validation_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A payload failing inbound validation is discarded without logging values."""
    caplog.set_level(logging.DEBUG)
    subscriber = InMemoryMessageSubscriber()
    gateway = InboundEventGateway(subscriber, MessageBus())
    gateway.register_translation(
        "invoices.issued", InvoiceIssuedIntegration, _to_domain
    )

    await subscriber.simulate_message(
        "invoices.issued",
        {"event_id": str(uuid4()), "invoice_id": "i1", "amount": CONTENT_SENTINEL},
    )

    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert_absent_from_logs(caplog)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-118 INF-10: hydration warnings and UpcastError embed the failing value",
)
def test_sentinel_absent_from_hydration_warning_and_upcast_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Saga hydration logs no payload value, and UpcastError's own text names none."""
    caplog.set_level(logging.DEBUG)

    hydrated = hydrate_command(
        __name__, "ChargeInvoice", {"invoice_id": "i1", "amount": CONTENT_SENTINEL}
    )
    with pytest.raises(UpcastError) as raised:
        InvoiceV1ToV2().upcast({"invoice_id": "i1", "amount": CONTENT_SENTINEL})

    assert hydrated is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert_absent_from_logs(caplog)
    assert_absent_from_strings([str(raised.value), repr(raised.value)])


# ── INF-15: reprs of library-owned payload fields ──────────────────────


def test_sentinel_absent_from_payload_reprs() -> None:
    """Library-owned payload fields stay out of repr() and str().

    OutboxEntry.payload, GenericDomainEvent.data and CompensationRecord.data.
    """
    payload = {"invoice_id": "i1", "note": CONTENT_SENTINEL}
    entry = OutboxEntry(
        message_id="m1",
        topic="invoices.issued",
        payload=payload,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    legacy = GenericDomainEvent(type="InvoiceIssued", data=payload)
    compensation = CompensationRecord(command_type="RefundInvoice", data=payload)

    assert_absent_from_reprs(entry, legacy, compensation, [entry, legacy, compensation])
    assert entry.payload == payload
    assert legacy.data == payload
    assert compensation.data == payload
