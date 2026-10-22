"""DDD-03..09, DDD-12, DDD-18 probes."""
from typing import ClassVar, NewType, Optional
from uuid import UUID

from pydomain.ddd import DomainError, DomainEvent, Entity, IdGenerator, Uuid7Generator


def attempt(label, fn):
    try:
        r = fn()
        print(f"{label}: OK -> {r!r}")
        return r
    except Exception as e:  # noqa: BLE001
        print(f"{label}: {type(e).__name__}: {str(e).splitlines()[0]}")
        return None


print("--- DDD-03 ---")
OrderId = NewType("OrderId", UUID)


class OrderN(Entity[OrderId]):
    pass


attempt("Entity[NewType] auto id", lambda: type(OrderN().id).__name__)

type InvoiceId = UUID


class InvoiceAlias(Entity[InvoiceId]):
    pass


attempt("Entity[PEP695 alias] auto id", lambda: type(InvoiceAlias().id).__name__)


class GenericSub[T](Entity[T]):
    pass


attempt("unparametrised GenericSub() auto id", lambda: type(GenericSub().id).__name__)
attempt("GenericSub[UUID]() auto id", lambda: type(GenericSub[UUID]().id).__name__)
print("annotation seen for GenericSub:", GenericSub.__pydantic_fields__["id"].annotation)
print("annotation seen for OrderN:", OrderN.__pydantic_fields__["id"].annotation)

print("--- DDD-04 ---")


class OptE(Entity[Optional[UUID]]):
    pass


class NoneE(Entity[None]):
    pass


ann = OptE.__pydantic_fields__["id"].annotation
print("Optional annotation origin:", getattr(ann, "__origin__", None), "args:", getattr(ann, "__args__", ()))
attempt("Entity[Optional[UUID]] auto id", lambda: OptE().id)
attempt("Entity[UUID|None] auto id", lambda: Entity[UUID | None].__pydantic_fields__["id"].annotation)
na = NoneE.__pydantic_fields__["id"].annotation
print("Entity[None] annotation:", na, "origin:", getattr(na, "__origin__", None))
attempt("Entity[None] auto id", lambda: NoneE().id)

print("--- DDD-05 ---")


class Order(Entity[UUID]):
    total: int = 0


d = {"total": 5}
a = Order.model_validate(d)
print("dict after model_validate:", sorted(d))
b = Order.model_validate(d)
print("reused dict -> same id:", a.id == b.id, "entities equal:", a == b)
d2 = {"total": 5}
Order(**d2)
print("kwargs ctor mutates caller dict:", "id" in d2)

print("--- DDD-06 ---")


class MaybeE(Entity[UUID | None]):
    name: str = ""


x = MaybeE(id=None, name="a")
y = MaybeE(id=None, name="b")
print("two id=None equal:", x == y, "set size:", len({x, y}))
z = MaybeE(name="auto")
print("UUID|None omitted id -> auto:", z.id)

print("--- DDD-07 ---")
o = Order(total=1)
s = {o}
o.id = "not-a-uuid"
print("assign non-UUID id accepted:", repr(o.id), "| o in set:", o in s)
print("validate_assignment:", Order.model_config.get("validate_assignment"))

print("--- DDD-08 ---")


class FixedGen:
    def __init__(self, v):
        self.v = v

    def generate(self):
        return self.v


U1 = UUID("00000000-0000-4000-8000-000000000001")
U2 = UUID("00000000-0000-4000-8000-000000000002")


class Product(Entity[UUID]):
    _id_generator: ClassVar[IdGenerator[UUID]] = FixedGen(U1)


orig = Entity._id_generator
try:
    Product.configure(id_generator=FixedGen(U2))
    print("Product.configure -> Entity._id_generator replaced:", Entity._id_generator is not orig)
    print("Order (sibling) now gets:", Order().id)
    print("Product gets its own override (configure ignored):", Product().id)
finally:
    Entity._id_generator = orig

print("--- DDD-09 ---")


class Shipped(DomainEvent):
    _id_generator: ClassVar[IdGenerator[UUID]] = FixedGen(U1)


print("subclass override honoured?", Shipped().event_id == U1, Shipped().event_id)
evorig = DomainEvent._id_generator
try:
    Shipped.configure(id_generator=FixedGen(U2))
    print("Shipped.configure sets base:", DomainEvent._id_generator is not evorig)
finally:
    DomainEvent._id_generator = evorig

print("--- DDD-12 ---")
attempt("Entity version=-5", lambda: Order(version=-5).version)
attempt("DomainEvent event_version=-1", lambda: Shipped(event_version=-1).event_version)

print("--- DDD-18 ---")


class SpecialOrder(Order):
    pass


p = Order(id=U1)
c = SpecialOrder(id=U1)
print("parent==child:", p == c, "hash equal:", hash(p) == hash(c), "set size:", len({p, c}))
print("parent hash == hash(id):", hash(p) == hash(U1), "equal to bare UUID:", p == U1)
