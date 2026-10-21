"""Probes: topic collisions after a domain type moves topic; translator class checks."""
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry
from pydomain.ddd.domain_event import DomainEvent


class A(DomainEvent): ...
class B(DomainEvent): ...
class X(IntegrationEvent): ...
class Y(IntegrationEvent): ...
class XSub(X): ...


def tr(cls):
    return lambda e: cls()


def case(label, steps):
    r = OutboundEventRegistry()
    try:
        for d, i, t in steps:
            r.register_translation(d, i, t, tr(i))
        print(f"{label}: ok; resolve:", {t: r.resolve(t).__name__ for _, _, t in steps})
    except ValueError as exc:
        print(f"{label}: ValueError: {exc}")


case("A moves t1->t2, then B binds t1 to Y", [(A, X, "t1"), (A, X, "t2"), (B, Y, "t1")])
case("A t1 X, A t1 Y (same type overwrite)", [(A, X, "t1"), (A, Y, "t1")])
case("A,B share t1 X; A switches to Y", [(A, X, "t1"), (B, X, "t1"), (A, Y, "t1")])
case("A t1 X; A t1 Y; B t1 X", [(A, X, "t1"), (A, Y, "t1"), (B, X, "t1")])
case("A t1 X; B t1 XSub (subclass)", [(A, X, "t1"), (B, XSub, "t1")])
case("A,B share t1 X; A moves to t2 Y; B t1 Y", [(A, X, "t1"), (B, X, "t1"), (A, Y, "t2"), (B, Y, "t1")])

# Same-type overwrite leaves the old topic resolvable
r = OutboundEventRegistry()
r.register_translation(A, X, "t1", tr(X))
r.register_translation(A, Y, "t1", tr(Y))
print("t1 after same-type overwrite resolves to", r.resolve("t1").__name__, "(stored X rows rehydrate as Y)")

# translator returns a non-event / subclass
r = OutboundEventRegistry()
r.register_translation(A, X, "t1", lambda e: XSub())
a = A().stamp(correlation_id=__import__("uuid").uuid4(), causation_id=__import__("uuid").uuid4()) if hasattr(A, "stamp") else A()
for label, fn in [("subclass", lambda e: XSub()), ("dict", lambda e: {"x": 1}), ("None", lambda e: None)]:
    r.register_translation(A, X, "t1", fn)
    try:
        print(label, "->", r.to_entries([a]))
    except TypeError as exc:
        print(label, "-> TypeError:", exc)
