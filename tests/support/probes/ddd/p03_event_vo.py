"""DDD-10, DDD-11, DDD-13 / A18 / A19 probes."""
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import field_validator

from pydomain.ddd import DomainEvent, ValueObject


def attempt(label, fn):
    try:
        r = fn()
        print(f"{label}: OK -> {r!r}")
        return r
    except Exception as e:  # noqa: BLE001
        print(f"{label}: {type(e).__name__}: {str(e).splitlines()[0]}")
        return None


class InvoiceIssued(DomainEvent):
    invoice_id: UUID
    lines: list[int]


print("--- DDD-10 ---")
e = InvoiceIssued(invoice_id=uuid4(), lines=[1, 2])
s = attempt("stamp(correlation_id='x')", lambda: e.stamp(correlation_id="x", causation_id=123))  # type: ignore[arg-type]
if s is not None:
    print("  stamped correlation_id type:", type(s.correlation_id).__name__, "causation:", type(s.causation_id).__name__)
    print("  round trip of stamped:", attempt("  model_validate(dump)", lambda: InvoiceIssued.model_validate(s.model_dump())))
s2 = e.stamp(correlation_id=uuid4(), causation_id=uuid4())
print("payload list shared between original and stamp:", s2.lines is e.lines)
s2.lines.append(99)
print("original.lines after mutating stamped copy:", e.lines)
print("event_id/occurred_at preserved:", s2.event_id == e.event_id, s2.occurred_at == e.occurred_at)
e.lines.append(3)
print("frozen event list mutable in place:", e.lines, "| hashable:", attempt("hash(event)", lambda: hash(e)))

print("--- DDD-11 ---")
naive = attempt("occurred_at naive", lambda: InvoiceIssued(invoice_id=uuid4(), lines=[], occurred_at=datetime(2026, 1, 1)).occurred_at.tzinfo)
dflt = InvoiceIssued(invoice_id=uuid4(), lines=[])
print("default tz:", dflt.occurred_at.tzinfo)
attempt("compare naive vs aware", lambda: datetime(2026, 1, 1) < dflt.occurred_at)

print("--- DDD-13 / A18 / A19 ---")


class Basket(ValueObject):
    items: list[str]


b = Basket(items=["a"])
b.items.append("b")
print("frozen VO list mutated in place:", b.items)
attempt("hash(Basket)", lambda: hash(b))
attempt("{Basket} set membership", lambda: len({b}))


class Money(ValueObject):  # create-value-object.md pattern
    amount: int
    currency: str

    @field_validator("amount")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Amount cannot be negative")
        return v

    def multiply(self, factor: int) -> "Money":
        return self.model_copy(update={"amount": self.amount * factor})


m = Money(amount=100, currency="EUR")
attempt("Money(amount=-1) direct", lambda: Money(amount=-1, currency="EUR"))
bad = attempt("multiply(-3) via model_copy(update)", lambda: m.multiply(-3).amount)
attempt("model_copy(update={'amount': 'abc'})", lambda: m.model_copy(update={"amount": "abc"}).amount)
attempt("model_copy(update={'bogus': 1}) extra field", lambda: m.model_copy(update={"bogus": 1}).model_dump())
attempt("hash(Money)", lambda: hash(m) is not None)
