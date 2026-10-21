"""Probes: strict-JSON validation depth, cycles, bool/int, key content, stamp ids."""
import enum
import sys
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from pydomain._describe import describe
from pydomain.cqrs.integration_events import IntegrationEvent

SENT = "SENTINEL-c0ntent-7f3"


class E(IntegrationEvent):
    data: Any = None


class EL(IntegrationEvent):
    data: list = []


class ED(IntegrationEvent):
    data: dict = {}


def attempt(label, fn):
    try:
        r = fn()
        print(f"{label}: accepted -> {type(r).__name__}")
    except ValidationError as exc:
        s = str(exc)
        print(f"{label}: ValidationError; sentinel in str(exc)? {SENT in s}; in describe? {SENT in describe(exc)}")
        print("   msg:", exc.errors()[0]["msg"][:200])
    except BaseException as exc:  # noqa: BLE001
        print(f"{label}: {type(exc).__name__}: {str(exc)[:120]}")


# depth
for depth in (100, 500, 900, 2000, 10000):
    v: Any = 1
    for _ in range(depth):
        v = [v]
    attempt(f"Any nested list depth {depth}", lambda v=v: E(data=v))
    attempt(f"list nested depth {depth}", lambda v=v: EL(data=v))
    w: Any = 1
    for _ in range(depth):
        w = {"k": w}
    attempt(f"dict nested depth {depth}", lambda w=w: ED(data=w))

# cycle
c: dict = {}
c["self"] = c
attempt("dict cycle (Any)", lambda: E(data=c))
attempt("dict cycle (dict)", lambda: ED(data=c))
lc: list = []
lc.append(lc)
attempt("list cycle (list)", lambda: EL(data=lc))

# bool / int / enums / subclasses
attempt("bool True", lambda: E(data=True))
attempt("bool key", lambda: ED(data={True: 1}))
attempt("int key", lambda: ED(data={1: 1}))


class S(enum.StrEnum):
    A = "a"


class Ie(enum.IntEnum):
    A = 1


attempt("StrEnum value", lambda: E(data=S.A))
attempt("IntEnum nested", lambda: EL(data=[Ie.A]))
attempt("huge int", lambda: E(data=10**400))
attempt("bytes nested", lambda: EL(data=[b"x"]))
attempt("tuple nested", lambda: ED(data={"a": (1, 2)}))
attempt("NaN nested deep", lambda: ED(data={"a": [{"b": float("nan")}]}))

# content in a dict key, in the path
attempt("sentinel as key with bad value", lambda: ED(data={SENT: {1, 2}}))
attempt("sentinel as value beside bad value", lambda: ED(data={"note": SENT, "bad": {1}}))
attempt("sentinel string + NaN in another field", lambda: type("X", (IntegrationEvent,), {"__annotations__": {"a": str, "b": float}})(a=SENT, b=float("nan")))

# stamp
ev = E()
attempt("stamp UUID", lambda: ev.stamp(correlation_id=uuid4(), causation_id=str(uuid4())))  # type: ignore[arg-type]
attempt("stamp int", lambda: ev.stamp(correlation_id=1, causation_id="x"))  # type: ignore[arg-type]
attempt("stamp None", lambda: ev.stamp(correlation_id=None, causation_id=None))  # type: ignore[arg-type]
attempt("stamp sentinel object", lambda: ev.stamp(correlation_id=[SENT], causation_id="x"))  # type: ignore[arg-type]
print("recursionlimit", sys.getrecursionlimit())
