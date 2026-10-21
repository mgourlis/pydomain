"""INF-14 and INF-15 probes against the real EventRegistry / UpcasterRegistry. Synthetic data only."""

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import field_validator, model_validator

from pydomain.cqrs.outbox import OutboxEntry
from pydomain.cqrs.saga.state import CompensationRecord
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry
from pydomain.infrastructure.event_registry import EventRegistry, GenericDomainEvent

logging.basicConfig(level=logging.WARNING, format="LOG %(levelname)s %(name)s: %(message)s")
S = "SENTINEL-XYZ"


# ---------- documented upcaster shape (event-versioning.md): does NOT touch event_version
class OrderPlaced(DomainEvent):
    total_amount: int
    currency: str
    event_version: int = 3


class OrderPlacedV1ToV2(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2
    calls: ClassVar[int] = 0

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        OrderPlacedV1ToV2.calls += 1
        event["total_amount"] = event.pop("total")
        return event


class OrderPlacedV2ToV3(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 2
    target_version: ClassVar[int] = 3
    calls: ClassVar[int] = 0

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        OrderPlacedV2ToV3.calls += 1
        event.setdefault("currency", "EUR")
        return event


def inf14_c() -> None:
    print("--- INF-14 (c) stale event_version ---")
    ups = UpcasterRegistry()
    ups.register(OrderPlacedV1ToV2)
    ups.register(OrderPlacedV2ToV3)
    reg = EventRegistry(upcaster_registry=ups)
    reg.register(OrderPlaced)
    # A v1 stored row as serialize() of a v1 class would have written it (data carries event_version=1)
    v1 = {"type": "OrderPlaced", "version": 1,
          "data": {"event_id": "00000000-0000-7000-8000-000000000001",
                   "occurred_at": "2026-01-01T00:00:00Z", "event_version": 1, "total": 10}}
    e = reg.deserialize(v1)
    print("after deserialize: type", type(e).__name__, "event_version =", e.event_version,
          "| upcaster calls", OrderPlacedV1ToV2.calls, OrderPlacedV2ToV3.calls)
    again = reg.serialize(e)
    print("re-serialize version key =", again["version"], "data.event_version =", again["data"]["event_version"])
    try:
        e2 = reg.deserialize(again)
        print("second deserialize OK:", e2, "calls", OrderPlacedV1ToV2.calls)
    except Exception as exc:  # noqa: BLE001
        print("second deserialize raises", type(exc).__name__, "->", str(exc)[:160])
    # Variant: stored row has version key but data lacks event_version (e.g. hand-written row)
    v1b = {"type": "OrderPlaced", "version": 1,
           "data": {"event_id": "00000000-0000-7000-8000-000000000002",
                    "occurred_at": "2026-01-01T00:00:00Z", "total": 10}}
    e3 = reg.deserialize(v1b)
    print("variant without data.event_version -> event_version =", e3.event_version)

    # Idempotent-looking upcaster (tolerates already-upcast payload) -> silent double upcast
    class Bump(DomainEvent):
        n: int

    class BumpV1ToV2(EventUpcaster):
        source_type: ClassVar[str] = "Bump"
        source_version: ClassVar[int] = 1
        target_version: ClassVar[int] = 2

        def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
            event["n"] = event["n"] * 10  # e.g. unit change euros -> cents
            return event

    ups.register(BumpV1ToV2)
    reg.register(Bump)
    b = reg.deserialize({"type": "Bump", "version": 1, "data": {"event_version": 1, "n": 5}})
    b2 = reg.deserialize(reg.serialize(b))
    print("non-idempotent upcaster: first n =", b.n, "after round trip n =", b2.n, "event_version", b2.event_version)


def inf14_a() -> None:
    print("--- INF-14 (a) unregistered never upcast ---")
    seen: list[int] = []

    class GoneV1ToV2(EventUpcaster):
        source_type: ClassVar[str] = "Gone"
        source_version: ClassVar[int] = 1
        target_version: ClassVar[int] = 2

        def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
            seen.append(1)
            event["new"] = event.pop("old")
            return event

    ups = UpcasterRegistry()
    ups.register(GoneV1ToV2)
    reg = EventRegistry(upcaster_registry=ups)
    g = reg.deserialize({"type": "Gone", "version": 1, "data": {"old": "x"}})
    print("result", repr(g), "| upcaster ran:", bool(seen))


def inf14_b() -> None:
    print("--- INF-14 (b) KeyError relabelled as unregistered ---")

    class Lookup(DomainEvent):
        code: str

        @field_validator("code")
        @classmethod
        def known(cls, v: str) -> str:
            return {"A": "alpha"}[v]  # KeyError on an unknown code

    class Before(DomainEvent):
        x: int

        @model_validator(mode="before")
        @classmethod
        def need(cls, data: Any) -> Any:
            data["x"] = data["legacy_x"]  # KeyError if missing
            return data

    class Raw(DomainEvent):
        y: int

    class RawUp(EventUpcaster):  # overrides upcast() directly, bypassing UpcastError wrap
        source_type: ClassVar[str] = "Raw"
        source_version: ClassVar[int] = 1
        target_version: ClassVar[int] = 2

        def upcast(self, event: dict[str, Any]) -> dict[str, Any]:
            event["y"] = event["old_y"]
            return event

    class Wrapped(DomainEvent):
        z: int

    class WrappedUp(EventUpcaster):
        source_type: ClassVar[str] = "Wrapped"
        source_version: ClassVar[int] = 1
        target_version: ClassVar[int] = 2

        def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
            event["z"] = event["old_z"]
            return event

    ups = UpcasterRegistry()
    ups.register(RawUp)
    ups.register(WrappedUp)
    reg = EventRegistry(upcaster_registry=ups)
    for c in (Lookup, Before, Raw, Wrapped):
        reg.register(c)
    cases = [("Lookup", {"code": "B"}), ("Before", {}), ("Raw", {}), ("Wrapped", {})]
    for name, data in cases:
        try:
            r = reg.deserialize({"type": name, "version": 1, "data": data})
            print(name, "registered ->", type(r).__name__, repr(r))
        except Exception as exc:  # noqa: BLE001
            print(name, "registered -> raises", type(exc).__name__, str(exc)[:120])


def inf14_d() -> None:
    print("--- INF-14 (d) rename ---")
    class OrderPlacedRenamed(DomainEvent):
        n: int
    reg = EventRegistry()
    reg.register(OrderPlacedRenamed)
    r = reg.deserialize({"type": "OldOrderPlaced", "version": 1, "data": {"n": 1}})
    print("old name ->", type(r).__name__)


def inf15() -> None:
    print("--- INF-15 repr/str ---")
    o = OutboxEntry(message_id="m-1", topic="t", payload={"note": S}, occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    g = GenericDomainEvent(type="Gone", data={"note": S})
    c = CompensationRecord(command_type="Undo", data={"note": S})
    for label, obj in (("OutboxEntry", o), ("GenericDomainEvent", g), ("CompensationRecord", c)):
        print(label, "repr has sentinel:", S in repr(obj), "| str has sentinel:", S in str(obj))
        print("   repr:", repr(obj)[:220])
    logging.getLogger("probe").warning("relay failed for %r", o)


if __name__ == "__main__":
    inf14_a()
    inf14_b()
    inf14_c()
    inf14_d()
    inf15()
