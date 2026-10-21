"""P11: AggregateLockingBehavior under the trio backend (was backend-neutral on dev)."""
import anyio
from pydomain.cqrs import Command, CommandResult
from pydomain.cqrs.behaviors import AggregateLockingBehavior, MessageContext
from pydomain.cqrs.locking import DictLockKeyResolver


class R(CommandResult):
    r: str


class C(Command[R]):
    sku: str


class AnyioLocks:
    def __init__(self) -> None:
        self.locks: dict[str, anyio.Lock] = {}

    async def acquire(self, key: str) -> None:
        await self.locks.setdefault(key, anyio.Lock()).acquire()

    async def release(self, key: str) -> None:
        self.locks[key].release()


async def main() -> None:
    res = DictLockKeyResolver()
    res.register(C, lambda c: [f"stock:{c.sku}"])
    p = AnyioLocks()
    b = AggregateLockingBehavior(p, res)

    async def h(c, u):
        return R(r="x")

    async def nxt() -> str:
        return "ok"

    try:
        print("result:", await b.handle(MessageContext(message=C(sku="a"), handler=h), nxt))
    except BaseException as e:
        print("raised:", type(e).__name__, e)
    print("stock:a still held:", p.locks["stock:a"].locked())


anyio.run(main, backend="trio")
