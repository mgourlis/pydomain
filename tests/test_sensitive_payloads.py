"""Payload values never reach library-built errors, logs or reprs (ADR-071).

The cross-cutting content check of the 0.3.0 audit (audit plan §4.6, rows
INF-10 and INF-15): :data:`CONTENT_SENTINEL` goes into a payload, the library
fails on it, and the sentinel must be absent from what the library stored,
logged, raised or printed. The paths span the outbox relay, the inbound
gateway, saga hydration and the saga manager, upcasting, and the models that
carry payloads, so the checks live here rather than in one module's folder.
The exact text of each description is tested next to its module.
"""

from __future__ import annotations

import enum
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal, cast
from uuid import uuid1, uuid4

import anyio
import pytest
from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from pydomain.cqrs.behaviors import IdempotencyBehavior
from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.cqrs.saga.hydration import hydrate_command
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import CompensationRecord, SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.exceptions import UpcastError
from pydomain.es.upcasting import EventUpcaster
from pydomain.infrastructure.event_registry import GenericDomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import (
    FakeOutboxStore,
    FakeProcessedMessageStore,
    FakeUnitOfWork,
    InMemoryMessageBroker,
    InMemoryMessageSubscriber,
)
from pydomain.testing.fake_saga_repository import FakeSagaRepository
from tests.saga.conftest import as_dispatcher
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


class InvoiceIssuedV4Correlated(DomainEvent):
    """Accepts only version-4 correlation ids, so stamp() rejects a v1 UUID."""

    invoice_id: str
    correlation_id: UUID4 | None = None  # type: ignore[assignment]


def _to_v4_correlated(event: InvoiceIssuedIntegration) -> DomainEvent:
    return InvoiceIssuedV4Correlated(invoice_id=event.invoice_id)


@pytest.mark.parametrize(
    ("event_id", "correlation_id", "translator"),
    [
        pytest.param(str(uuid4()), CONTENT_SENTINEL, _to_domain, id="correlation_id"),
        pytest.param(CONTENT_SENTINEL, str(uuid4()), _to_domain, id="event_id"),
        pytest.param(CONTENT_SENTINEL, CONTENT_SENTINEL, _to_domain, id="both"),
        pytest.param(
            str(uuid4()), f"{{{CONTENT_SENTINEL}}}", _to_domain, id="in-braces"
        ),
        pytest.param(
            str(uuid4()), str(uuid1()), _to_v4_correlated, id="event-rejects-v1"
        ),
    ],
)
@pytest.mark.anyio
async def test_sentinel_absent_from_inbound_tracing_ids_warning(
    caplog: pytest.LogCaptureFixture,
    event_id: str,
    correlation_id: str,
    translator: Any,
) -> None:
    """Malformed or rejected tracing ids are reported by name, never by value."""
    caplog.set_level(logging.DEBUG)
    subscriber = InMemoryMessageSubscriber()
    gateway = InboundEventGateway(subscriber, MessageBus())
    gateway.register_translation(
        "invoices.issued", InvoiceIssuedIntegration, translator
    )

    await subscriber.simulate_message(
        "invoices.issued",
        {
            "event_id": event_id,
            "correlation_id": correlation_id,
            "invoice_id": "i1",
            "amount": 1,
        },
    )

    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert_absent_from_logs(caplog)


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


def test_sentinel_absent_from_saga_state_repr() -> None:
    """pending_commands and failed_compensations stay out of SagaState's repr()."""
    command = {"command_type": "ChargeInvoice", "data": {"note": CONTENT_SENTINEL}}
    state = SagaState(
        saga_type="_InvoiceSaga",
        pending_commands=[command],
        failed_compensations=[{**command, "module_name": __name__, "error": "x"}],
    )

    assert_absent_from_reprs(state)
    assert state.pending_commands == [command]
    assert state.failed_compensations[0]["data"] == command["data"]


# ── INF-10, decided paths (ADR-071 Q3, Q5) ─────────────────────────────


class _LeakyBroker(InMemoryMessageBroker):
    """A broker adapter whose exception text quotes the message body."""

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        raise RuntimeError(f"rejected message body {event.model_dump()}")


class _FetchFailsOnceStore(FakeOutboxStore):
    """An outbox whose first fetch fails with content in its message."""

    def __init__(self) -> None:
        super().__init__()
        self.fetches = 0
        self.fetched_again = anyio.Event()

    async def fetch_unpublished(self, limit: int) -> list[OutboxEntry]:
        self.fetches += 1
        if self.fetches == 1:
            raise RuntimeError(f"row could not be read: {CONTENT_SENTINEL}")
        self.fetched_again.set()
        return []


@pytest.mark.anyio
async def test_sentinel_absent_from_relay_broker_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broker error quoting the payload reaches neither mark_failed nor the log."""
    caplog.set_level(logging.DEBUG)
    store = FakeOutboxStore()
    await store.append(
        [
            OutboxEntry(
                message_id="m1",
                topic="invoices.issued",
                payload={"invoice_id": CONTENT_SENTINEL, "amount": 1},
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]
    )

    await OutboundEventGateway(store, _LeakyBroker(), _outbound_registry()).run_once()

    assert [message_id for message_id, _, _ in store.failures] == ["m1"]
    assert_absent_from_strings(error for _, error, _ in store.failures)
    assert_absent_from_logs(caplog)


@pytest.mark.anyio
async def test_sentinel_absent_from_relay_pass_failure_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A store failure in a relay pass is logged without its message or traceback."""
    caplog.set_level(logging.DEBUG)
    store = _FetchFailsOnceStore()
    gateway = OutboundEventGateway(
        store,
        InMemoryMessageBroker(),
        _outbound_registry(),
        poll_interval_seconds=0.01,
        failure_backoff_seconds=0,
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(gateway.run)
        with anyio.fail_after(5):
            await store.fetched_again.wait()
        await gateway.stop()

    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert_absent_from_logs(caplog)


class InvoiceOpened(DomainEvent):
    invoice_id: str


class InvoiceDisputed(DomainEvent):
    invoice_id: str


class RefundInvoice(Command[EmptyCommandResult]):
    invoice_id: str


class _InvoiceSaga(Saga[SagaState]):
    """Charges on open, registers a refund, and fails on a dispute."""

    listens_to: ClassVar[list[type[DomainEvent]]] = [InvoiceOpened, InvoiceDisputed]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(InvoiceOpened, handler=self._charge)
        self.on(InvoiceDisputed, handler=self._dispute)

    async def _charge(self, event: DomainEvent) -> None:
        self.dispatch(ChargeInvoice(invoice_id="i1", amount=1))
        self.add_compensation(RefundInvoice(invoice_id="i1"), "Refund the charge")

    async def _dispute(self, event: DomainEvent) -> None:
        raise RuntimeError(f"dispute note: {CONTENT_SENTINEL}")

    async def on_timeout(self) -> None:
        raise RuntimeError(f"reminder text: {CONTENT_SENTINEL}")


class InvoiceOverdue(DomainEvent):
    invoice_id: str


class SendReminder(Command[EmptyCommandResult]):
    invoice_id: str


class _ReminderSaga(Saga[SagaState]):
    """Sends a reminder on an overdue invoice; keeps the default on_timeout()."""

    listens_to: ClassVar[list[type[DomainEvent]]] = [InvoiceOverdue]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(InvoiceOverdue, handler=self._remind)

    async def _remind(self, event: DomainEvent) -> None:
        self.dispatch(SendReminder(invoice_id="i1"))


class _RefusingRefundBus:
    """A command bus double: charges pass, refunds and reminders fail quoting data."""

    async def dispatch(self, command: Command[Any]) -> Any:
        if isinstance(command, RefundInvoice):
            raise RuntimeError(f"refund refused: {CONTENT_SENTINEL}")
        if isinstance(command, SendReminder):
            raise RuntimeError(f"reminder rejected: {CONTENT_SENTINEL}")
        return EmptyCommandResult()


@pytest.mark.anyio
async def test_sentinel_absent_from_saga_manager_stored_errors() -> None:
    """Stored saga errors and the suspension reason carry no exception text."""
    repository = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(_InvoiceSaga)
    registry.register_saga(_ReminderSaga)
    manager = SagaManager(
        repository=repository,
        registry=registry,
        command_bus=as_dispatcher(cast(CommandBus, _RefusingRefundBus())),
    )
    correlation_id = uuid4()
    await manager.handle(InvoiceOpened(invoice_id="i1", correlation_id=correlation_id))
    await manager.handle(
        InvoiceDisputed(invoice_id="i1", correlation_id=correlation_id)
    )
    failed = await repository.find_by_correlation_id(correlation_id, "_InvoiceSaga")

    suspended = SagaState(
        saga_type="_InvoiceSaga",
        status=SagaStatus.SUSPENDED,
        timeout_at=datetime.now(UTC) - timedelta(hours=1),
    )
    await repository.save(suspended)
    reminder_id = uuid4()
    await manager.handle(InvoiceOverdue(invoice_id="i1", correlation_id=reminder_id))
    stalled = await repository.find_by_correlation_id(reminder_id, "_ReminderSaga")
    assert stalled is not None
    assert stalled.status == SagaStatus.SUSPENDED
    stalled_reason = stalled.suspension_reason or ""
    stalled.timeout_at = datetime.now(UTC) - timedelta(hours=1)
    await repository.save(stalled)
    await manager.process_timeouts()
    timed_out = await repository.get_by_id(suspended.id)
    reminder_timed_out = await repository.get_by_id(stalled.id)

    assert failed is not None
    assert timed_out is not None
    assert reminder_timed_out is not None
    assert reminder_timed_out.status == SagaStatus.FAILED
    assert len(failed.failed_compensations) == 1
    assert_absent_from_strings(
        [
            failed.error or "",
            failed.failed_compensations[0]["error"],
            timed_out.error or "",
            stalled_reason,
            reminder_timed_out.error or "",
        ]
    )


# ── Review probes (PR #107): saga dispatch failures through a real bus ──


class InvoiceLine(BaseModel):
    """A nested model whose validator quotes the value it rejects."""

    sku: str
    quantity: int

    @field_validator("sku")
    @classmethod
    def _no_sentinel(cls, value: str) -> str:
        if value.startswith("SENTINEL"):
            msg = f"bad sku {value}"
            raise ValueError(msg)
        return value


def _raise_validation_error() -> None:
    InvoiceLine.model_validate({"sku": CONTENT_SENTINEL, "quantity": CONTENT_SENTINEL})


def _raise_exception_group() -> None:
    try:
        raise KeyError(CONTENT_SENTINEL)
    except KeyError as cause:
        raise ExceptionGroup(
            CONTENT_SENTINEL, [ValueError(CONTENT_SENTINEL)]
        ) from cause


@pytest.mark.parametrize(
    "fail", [_raise_validation_error, _raise_exception_group], ids=["ve", "group"]
)
@pytest.mark.anyio
async def test_sentinel_absent_after_a_real_bus_dispatch_failure_and_timeout(
    fail: Any,
) -> None:
    """A handler failure stays out of suspension_reason, the timeout error and repr."""
    bus = CommandBus()

    async def send_reminder(command: Any, uow: Any = None) -> EmptyCommandResult:
        fail()
        return EmptyCommandResult()

    bus.register(SendReminder, send_reminder, uow_factory=lambda: FakeUnitOfWork())
    repository = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(_ReminderSaga)
    manager = SagaManager(
        repository=repository, registry=registry, command_bus=as_dispatcher(bus)
    )
    correlation_id = uuid4()

    await manager.handle(InvoiceOverdue(invoice_id="i1", correlation_id=correlation_id))
    stalled = await repository.find_by_correlation_id(correlation_id, "_ReminderSaga")
    assert stalled is not None
    assert stalled.status == SagaStatus.SUSPENDED
    reason = stalled.suspension_reason or ""
    stalled.timeout_at = datetime.now(UTC) - timedelta(hours=1)
    await repository.save(stalled)
    await manager.process_timeouts()
    timed_out = await repository.get_by_id(stalled.id)

    assert timed_out is not None
    assert timed_out.status == SagaStatus.FAILED
    assert_absent_from_strings([reason, timed_out.error or ""])
    assert_absent_from_reprs(timed_out)


class _QuotingBus:
    """Raises a CommandExecutionError whose own message quotes data, with no cause."""

    async def dispatch(self, command: Command[Any]) -> Any:
        raise CommandExecutionError(command, f"failed for {CONTENT_SENTINEL}")


@pytest.mark.anyio
async def test_sentinel_absent_when_command_execution_error_has_no_cause() -> None:
    repository = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(_ReminderSaga)
    manager = SagaManager(
        repository=repository,
        registry=registry,
        command_bus=as_dispatcher(cast(CommandBus, _QuotingBus())),
    )
    correlation_id = uuid4()

    await manager.handle(InvoiceOverdue(invoice_id="i1", correlation_id=correlation_id))

    stalled = await repository.find_by_correlation_id(correlation_id, "_ReminderSaga")
    assert stalled is not None
    assert stalled.suspension_reason == "Dispatch failed: CommandExecutionError"


class _QuotingBusWithValidationCause:
    """Raises a CommandExecutionError quoting a ValidationError that quotes data."""

    async def dispatch(self, command: Command[Any]) -> Any:
        try:
            _raise_validation_error()
        except ValidationError as cause:
            raise CommandExecutionError(command, f"failed: {cause}") from cause


@pytest.mark.anyio
async def test_sentinel_absent_when_command_execution_error_quotes_its_cause() -> None:
    """The suspension reason describes the cause by location and code only."""
    repository = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(_ReminderSaga)
    manager = SagaManager(
        repository=repository,
        registry=registry,
        command_bus=cast(CommandBus, _QuotingBusWithValidationCause()),
    )
    correlation_id = uuid4()

    await manager.handle(InvoiceOverdue(invoice_id="i1", correlation_id=correlation_id))

    stalled = await repository.find_by_correlation_id(correlation_id, "_ReminderSaga")
    assert stalled is not None
    assert stalled.status == SagaStatus.SUSPENDED
    assert stalled.suspension_reason == (
        "Dispatch failed: ValidationError: 2 errors for InvoiceLine at sku "
        "(value_error), quantity (int_parsing)"
    )


# ── Review probes (PR #107): payload shapes on the relay, inbound and upcast paths ──


class Currency(enum.Enum):
    EUR = "EUR"


class InvoiceAudit(IntegrationEvent):
    """An integration event with nested, enum, literal, list, date and dict fields."""

    model_config = ConfigDict(extra="ignore")

    lines: list[InvoiceLine] = []
    currency: Currency = Currency.EUR
    stage: Literal["draft", "final"] = "draft"
    amounts: list[int] = []
    issued_at: datetime | None = None
    by_sku: dict[str, InvoiceLine] = {}
    reference: str = ""

    @field_validator("reference")
    @classmethod
    def _known_reference(cls, value: str) -> str:
        if value.startswith("SENTINEL"):
            raise PydanticCustomError(
                "unknown_reference", "unknown reference {value}", {"value": value}
            )
        return value

    @model_validator(mode="after")
    def _no_placeholder_quantity(self) -> InvoiceAudit:
        if any(line.quantity == 999 for line in self.lines):
            msg = f"placeholder quantity in {self.lines!r}"
            raise ValueError(msg)
        return self


_NESTED_INVALID = {
    "lines": [{"sku": CONTENT_SENTINEL, "quantity": CONTENT_SENTINEL}],
    "currency": CONTENT_SENTINEL,
    "stage": CONTENT_SENTINEL,
    "amounts": [1, CONTENT_SENTINEL],
    "issued_at": CONTENT_SENTINEL,
    "by_sku": {"a1": {"sku": CONTENT_SENTINEL, "quantity": 1}},
}
_MODEL_LEVEL_INVALID = {
    "lines": [{"sku": "a1", "quantity": 999}],
    "note": CONTENT_SENTINEL,
}
_CUSTOM_MESSAGE_INVALID = {"reference": CONTENT_SENTINEL}


class _GroupBroker(InMemoryMessageBroker):
    """Raises an ExceptionGroup whose message, members and cause quote content."""

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        try:
            raise ValueError(CONTENT_SENTINEL)
        except ValueError as cause:
            raise ExceptionGroup(
                f"rejected {CONTENT_SENTINEL}", [RuntimeError(CONTENT_SENTINEL)]
            ) from cause


class _ValidatingBroker(InMemoryMessageBroker):
    """Raises a ValidationError of its own while publishing."""

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        _raise_validation_error()


def _registry_for(integration_class: type[IntegrationEvent]) -> OutboundEventRegistry:
    registry = OutboundEventRegistry()
    registry.register_translation(
        InvoiceIssued, integration_class, "audits", lambda event: integration_class()
    )
    return registry


@pytest.mark.parametrize(
    ("payload", "integration_class", "broker"),
    [
        pytest.param(
            _NESTED_INVALID, InvoiceAudit, InMemoryMessageBroker(), id="nested"
        ),
        pytest.param(
            _MODEL_LEVEL_INVALID, InvoiceAudit, InMemoryMessageBroker(), id="model"
        ),
        pytest.param(
            _CUSTOM_MESSAGE_INVALID,
            InvoiceAudit,
            InMemoryMessageBroker(),
            id="custom-message",
        ),
        pytest.param(
            {"invoice_id": CONTENT_SENTINEL, "amount": 1},
            InvoiceIssuedIntegration,
            _GroupBroker(),
            id="broker-group",
        ),
        pytest.param(
            {"invoice_id": CONTENT_SENTINEL, "amount": 1},
            InvoiceIssuedIntegration,
            _ValidatingBroker(),
            id="broker-validation-error",
        ),
    ],
)
@pytest.mark.anyio
async def test_sentinel_absent_from_relay_for_every_payload_shape(
    caplog: pytest.LogCaptureFixture,
    payload: dict[str, Any],
    integration_class: type[IntegrationEvent],
    broker: InMemoryMessageBroker,
) -> None:
    """No traceback, log argument, mark_failed error or dead letter carries content."""
    caplog.set_level(logging.DEBUG)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = FakeOutboxStore(clock=lambda: now)
    await store.append(
        [OutboxEntry(message_id="m1", topic="audits", payload=payload, occurred_at=now)]
    )
    gateway = OutboundEventGateway(
        store,
        broker,
        _registry_for(integration_class),
        base_delay=timedelta(0),
        max_attempts=2,
        clock=lambda: now,
    )

    await gateway.run_once()
    now += timedelta(minutes=1)
    await gateway.run_once()

    assert [message_id for message_id, _, _ in store.failures] == ["m1"]
    assert list(store.dead_lettered) == ["m1"]
    assert all(record.exc_info is None for record in caplog.records)
    assert_absent_from_strings(error for _, error, _ in store.failures)
    assert_absent_from_strings(store.dead_lettered.values())
    assert_absent_from_logs(caplog)  # message, args and extra fields


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_NESTED_INVALID, id="nested"),
        pytest.param(_MODEL_LEVEL_INVALID, id="model"),
        pytest.param(_CUSTOM_MESSAGE_INVALID, id="custom-message"),
    ],
)
@pytest.mark.anyio
async def test_sentinel_absent_from_inbound_logs_for_every_payload_shape(
    caplog: pytest.LogCaptureFixture, payload: dict[str, Any]
) -> None:
    caplog.set_level(logging.DEBUG)
    subscriber = InMemoryMessageSubscriber()
    gateway = InboundEventGateway(subscriber, MessageBus())
    gateway.register_translation(
        "audits", InvoiceAudit, lambda event: InvoiceIssued(invoice_id="i1", amount=1)
    )

    await subscriber.simulate_message("audits", {"event_id": str(uuid4()), **payload})

    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)
    assert_absent_from_logs(caplog)


class ChargeLines(Command[EmptyCommandResult]):
    lines: list[InvoiceLine]


def test_sentinel_absent_from_hydration_of_nested_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    hydrated = hydrate_command(
        __name__,
        "ChargeLines",
        {"lines": [{"sku": CONTENT_SENTINEL, "quantity": CONTENT_SENTINEL}]},
    )

    assert hydrated is None
    assert all(record.exc_info is None for record in caplog.records)
    assert_absent_from_logs(caplog)


class _FailingUpcaster(EventUpcaster):
    source_type: ClassVar[str] = "InvoiceIssued"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2
    failure: ClassVar[str] = "validation"

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        if self.failure == "validation":
            InvoiceLine.model_validate(event)
        if self.failure == "group":
            raise ExceptionGroup(CONTENT_SENTINEL, [KeyError(CONTENT_SENTINEL)])
        raise KeyError(CONTENT_SENTINEL)


@pytest.mark.parametrize("failure", ["validation", "group", "key"])
def test_sentinel_absent_from_upcast_error_for_every_failure(failure: str) -> None:
    """The message and args are clean; the user's exception stays the cause (B-32)."""
    upcaster = type("Upcaster", (_FailingUpcaster,), {"failure": failure})()

    with pytest.raises(UpcastError) as raised:
        upcaster.upcast({"sku": CONTENT_SENTINEL, "quantity": CONTENT_SENTINEL})

    error = raised.value
    assert_absent_from_strings([str(error), repr(error), *map(str, error.args)])
    assert error.__cause__ is not None


# ── DCE-143 (B-129): a failed idempotency record after the commit ──────


class _RecordFailsStore(FakeProcessedMessageStore):
    async def set(self, message_id: Any, result: Any) -> None:
        raise RuntimeError(CONTENT_SENTINEL)


@pytest.mark.anyio
@pytest.mark.xfail(
    strict=True,
    raises=CommandExecutionError,
    reason="DCE-143 B-129: a failing set runs before the commit and fails the dispatch",
)
async def test_sentinel_absent_from_failed_idempotency_record_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    bus = CommandBus()

    async def charge(command: ChargeInvoice, uow: Any) -> EmptyCommandResult:
        return EmptyCommandResult()

    bus.register(
        ChargeInvoice,
        charge,
        uow_factory=FakeUnitOfWork,
        behaviors=[IdempotencyBehavior(_RecordFailsStore())],
    )

    await bus.dispatch(ChargeInvoice(invoice_id=CONTENT_SENTINEL, amount=1))

    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert_absent_from_logs(caplog)
