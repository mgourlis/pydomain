import uuid
from uuid import UUID
from typing import NewType, Optional, Annotated, Union, Any, Literal, TypeVar
from pydantic import Field
from pydomain.ddd.entity import Entity
OrderId = NewType("OrderId", UUID)
type Y = UUID
type X = Y | int
type AliasAnn = Annotated[UUID, "x"]
TB = TypeVar("TB", bound=UUID)
cases = {"NewType": OrderId, "Optional[X]": Optional[X], "Ann|None": Annotated[UUID, "m"] | None,
 "Ann[NewType]|None": Annotated[OrderId, "m"] | None, "alias Ann": AliasAnn, "Any": Any, "Any|None": Any | None,
 "int": int, "Literal": Literal["a"], "list[UUID]": list[UUID], "TypeVar bound": TB, "NewType(int)": NewType("I", int),
 "str|None": str | None}
for n, a in cases.items():
    try:
        c = type("X", (Entity,), {"__annotations__": {"id": a}}); print(f"{n:20} ok {type(c().id).__name__}")
    except Exception as e: print(f"{n:20} {type(e).__name__}: {str(e)[:100]}")
class G[T](Entity[T]): pass
for n, cls in [("unbound", G), ("bare", type("B",(Entity,),{}))]:
    try: cls()
    except Exception as e: print(n, type(e).__name__)
# None generator for Optional
class NoneGen:
    def generate(self): return None
class OptE(Entity[UUID | None]):
    _id_generator = NoneGen()
try: print("None gen on Optional:", OptE().id)
except Exception as e: print("None gen", type(e).__name__)
class Cnt:
    n=0
    def generate(self): Cnt.n+=1; return uuid.uuid4()
class Bad(G):
    _id_generator = Cnt()
try: Bad()
except Exception: pass
print("generator called before failing guard:", Cnt.n)
