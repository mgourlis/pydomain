import sys
from common import *
from pydomain.cqrs.saga.hydration import hydrate_command

class Reserve(Command[EmptyCommandResult]):
    order_id: str
    quantity: int = 1          # v2 renamed from "qty"

class Outer:
    class Nested(Command[EmptyCommandResult]):
        order_id: str

# renamed field silently defaults
c = hydrate_command(__name__, "Reserve", {"order_id": "O", "qty": 50})
print(f"15 renamed field: hydrated={c!r}")
n = Outer.Nested(order_id="O")
print(f"15 nested: __name__={type(n).__name__} __qualname__={type(n).__qualname__} hydrate->{hydrate_command(type(n).__module__, type(n).__name__, n.model_dump())}")
# module named by persisted data is imported
before = "this_is_persisted_data_mod" in sys.modules
import pathlib, tempfile
tmp = tempfile.mkdtemp()
pathlib.Path(tmp, "this_is_persisted_data_mod.py").write_text("print('15 MODULE TOP-LEVEL CODE RAN')\n")
sys.path.insert(0, tmp)
hydrate_command("this_is_persisted_data_mod", "Anything", {})
# any importable stdlib module also loads
print("15 stdlib attr resolved as 'command':", hydrate_command("os", "getcwd", {}) )
