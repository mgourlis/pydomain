"""Reviewer probes for PR #107 (not for commit)."""

from __future__ import annotations

import enum
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal, cast
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from pydomain._describe import describe
from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.cqrs.saga.hydration import hydrate_command
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.exceptions import UpcastError
from pydomain.es.upcasting import EventUpcaster
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker, InMemoryMessageSubscriber
from pydomain.testing.fake_saga_repository import FakeSagaRepository
from tests.support.content_sentinel import (
    CONTENT_SENTINEL as S,
    assert_absent_from_logs,
    assert_absent_from_reprs,
    assert_absent_from_strings,
)


class Colour(enum.Enum):
    RED = "red"


class Line(BaseModel):
    sku: str
    qty: int

    @field_validator("sku")
    @classmethod
    def _v(cls, v: str) -> str:
        if v.startswith("SENT"):
            raise ValueError(f"bad sku {v}")
        return v


class Probe(IntegrationEvent):
    model_config = ConfigDict(extra="ignore")
    lines: list[Line] = []
    colour: Colour = Colour.RED
    kind: Literal["a", "b"] = "a"
    items: list[int] = []
    when: datetime | None = None
    nested: dict[str, Line] = {}

    @model_validator(mode="after")
    def _mv(self) -> Probe:
        if any(line.qty == 999 for line in self.lines):
            raise ValueError(f"model says {self.lines!r}")
        return self


class CustomErr(BaseModel):
    x: str

    @field_validator("x")
    @classmethod
    def _v(cls, v: str) -> str:
        raise PydanticCustomError("my_code", "custom {value}", {"value": v})


BAD = {
    "lines": [{"sku": S, "qty": S}],
    "colour": S,
    "kind": S,
    "items": [1, S],
    "when": S,
    "nested": {"k": {"sku": S, "qty": 1}},
}
MODEL_LEVEL = {"lines": [{"sku": "ok", "qty": 999}], "note": S}


def _ve(model: type[BaseModel], data: dict[str, Any]) -> ValidationError:
    try:
        model.model_validate(data)
    except ValidationError as e:
        return e
    raise AssertionError


def test_describe_variety() -> None:
    for model, data in [(Probe, BAD), (Probe, MODEL_LEVEL), (CustomErr, {"x": S})]:
        d = describe(_ve(model, data))
        print(d)
        assert S not in d
    # json mode
    try:
        Probe.model_validate_json('{"items": [1, "' + S + '"], "colour": "' + S + '"}')
    except ValidationError as e:
        print(describe(e))
        assert S not in describe(e)
    # exception group, chained
    try:
        try:
            raise KeyError(S)
        except KeyError as inner:
            raise ExceptionGroup(S, [ValueError(S), inner]) from inner
    except ExceptionGroup as g:
        assert describe(g) == "ExceptionGroup"


def _store_with(payload: dict[str, Any]) -> FakeOutboxStore:
    return FakeOutboxStore()


class _Reg(OutboundEventRegistry):
    pass


class Simple(IntegrationEvent):
    note: str = ""


def _registry(cls: type[IntegrationEvent] = Probe) -> OutboundEventRegistry:
    r = OutboundEventRegistry()

    class Ev(DomainEvent):
        x: int = 0

    r.register_translation(Ev, cls, "t", lambda e: cls())
    return r


class _GroupBroker(InMemoryMessageBroker):
    async def publish(self, topic: str, event: IntegrationEvent, **kw: Any) -> None:
        try:
            raise ValueError(S)
        except ValueError as cause:
            raise ExceptionGroup(f"grp {S}", [RuntimeError(S)]) from cause


class _VEBroker(InMemoryMessageBroker):
    async def publish(self, topic: str, event: IntegrationEvent, **kw: Any) -> None:
        Line.model_validate({"sku": S, "qty": S})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "broker"),
    [
        (BAD, InMemoryMessageBroker()),
        (MODEL_LEVEL, InMemoryMessageBroker()),
        ({"note": S}, _GroupBroker()),
        ({"note": S}, _VEBroker()),
    ],
)
async def test_relay(payload: dict[str, Any], broker: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = FakeOutboxStore(clock=lambda: now)
    await store.append([OutboxEntry(message_id="m", topic="t", payload=payload, occurred_at=now)])
    gw = OutboundEventGateway(store, broker, _registry(Simple if "note" in payload and payload is not MODEL_LEVEL else Probe), max_attempts=2, base_delay=timedelta(0), clock=lambda: now)
    await gw.run_once()
    now += timedelta(minutes=1)
    await gw.run_once()
    assert store.failures and store.dead_lettered
    for r in caplog.records:
        print(r.levelname, r.getMessage(), r.exc_info, r.args)
        assert r.exc_info is None
    assert_absent_from_strings(e for _, e, _ in store.failures)
    assert_absent_from_strings(store.dead_lettered.values())
    assert_absent_from_logs(caplog)
    # also when the caller logs the entry at the app level
    logging.getLogger("app").error("entry %s %r", store, list(store.dead_lettered))


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [BAD, MODEL_LEVEL])
async def test_inbound(payload: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sub = InMemoryMessageSubscriber()
    gw = InboundEventGateway(sub, MessageBus())

    class Ev(DomainEvent):
        x: int = 0

    gw.register_translation("t", Probe, lambda e: Ev())
    await sub.simulate_message("t", {"event_id": str(uuid4()), **payload})
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    for r in caplog.records:
        print(r.levelname, r.getMessage(), r.exc_info)
        assert r.exc_info is None
    assert_absent_from_logs(caplog)


@pytest.mark.anyio
async def test_inbound_tracing_ids(caplog: pytest.LogCaptureFixture) -> None:
    """Out of the decided scope? The invalid-tracing-ID warning logs payload values."""
    caplog.set_level(logging.DEBUG)
    sub = InMemoryMessageSubscriber()
    gw = InboundEventGateway(sub, MessageBus())

    class Ev(DomainEvent):
        x: int = 0

    gw.register_translation("t", Simple, lambda e: Ev())
    await sub.simulate_message("t", {"event_id": str(uuid4()), "correlation_id": S})
    for r in caplog.records:
        print(r.levelname, r.getMessage())
    assert_absent_from_logs(caplog)


class LineCmd(Command[EmptyCommandResult]):
    lines: list[Line]


def test_hydrate(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert hydrate_command(__name__, "LineCmd", {"lines": [{"sku": S, "qty": S}]}) is None
    assert hydrate_command(f"no_such_{S}_mod", "X", {}) is None  # module name is not payload
    for r in caplog.records:
        print(r.getMessage())
        assert r.exc_info is None
    assert_absent_from_logs(caplog.records[:1])


class _Up(EventUpcaster):
    source_type: ClassVar[str] = "E"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2
    mode: ClassVar[str] = "ve"

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "ve":
            Line.model_validate(event)
        if self.mode == "group":
            raise ExceptionGroup(S, [KeyError(S)])
        raise KeyError(S)


@pytest.mark.parametrize("mode", ["ve", "group", "key"])
def test_upcast(mode: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    up = type("U", (_Up,), {"mode": mode})()
    with pytest.raises(UpcastError) as raised:
        up.upcast({"sku": S, "qty": S})
    print(str(raised.value))
    assert_absent_from_strings([str(raised.value), repr(raised.value), *map(str, raised.value.args)])
    assert raised.value.__cause__ is not None
    # B-32: the cause keeps its text; logging with exc_info prints it (decided)
    logging.getLogger("app").error("upcast failed", exc_info=raised.value)
    with pytest.raises(AssertionError):
        assert_absent_from_logs(caplog)


# ---- saga: dispatch failure suspension reason -------------------------


class Opened(DomainEvent):
    oid: str


class Charge(Command[EmptyCommandResult]):
    oid: str


class _S(Saga[SagaState]):
    listens_to: ClassVar[list[type[DomainEvent]]] = [Opened]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(Opened, handler=self._go)

    async def _go(self, event: DomainEvent) -> None:
        self.dispatch(Charge(oid="o1"))


class _FailingBus:
    async def dispatch(self, command: Command[Any]) -> Any:
        try:
            Line.model_validate({"sku": S, "qty": S})
        except ValidationError as ve:
            raise CommandExecutionError(f"cmd failed {ve}") from ve


@pytest.mark.anyio
async def test_saga_dispatch_failure_suspension_reason(caplog: pytest.LogCaptureFixture) -> None:
    repo = FakeSagaRepository()
    reg = SagaRegistry()
    reg.register_saga(_S)
    mgr = SagaManager(repository=repo, registry=reg, command_bus=cast(CommandBus, _FailingBus()))
    cid = uuid4()
    try:
        await mgr.handle(Opened(oid="o1", correlation_id=cid))
    except Exception as e:  # noqa: BLE001
        print("raised", type(e).__name__)
    state = await repo.find_by_correlation_id(cid, "_S")
    assert state is not None
    print("status", state.status, "reason", state.suspension_reason)
    # stored error built by the saga manager
    assert_absent_from_strings([state.suspension_reason or ""])


@pytest.mark.anyio
async def test_saga_timeout_after_dispatch_failure() -> None:
    repo = FakeSagaRepository()
    reg = SagaRegistry()
    reg.register_saga(_S)
    mgr = SagaManager(repository=repo, registry=reg, command_bus=cast(CommandBus, _FailingBus()))
    st = SagaState(
        saga_type="_S",
        status=SagaStatus.SUSPENDED,
        suspension_reason=f"Dispatch failed: {S}",
        timeout_at=datetime.now(UTC) - timedelta(hours=1),
    )
    await repo.save(st)
    await mgr.process_timeouts()
    got = await repo.get_by_id(st.id)
    assert got is not None
    print("error", got.error)


def test_saga_state_repr_error_fields() -> None:
    st = SagaState(saga_type="x", error=f"E {S}", suspension_reason=f"R {S}")
    print(repr(st))
