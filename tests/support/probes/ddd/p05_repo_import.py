"""DDD-16, DDD-17, DDD-19 probes, plus DDD-05 consequence in the fake."""
import asyncio
import subprocess
import sys
from uuid import UUID

from pydomain.ddd import AggregateRoot, ConcurrencyError, Repository
from pydomain.testing.fake_repository import FakeRepository


class Order(AggregateRoot[UUID]):
    total: int = 0


print("--- DDD-16 ---")


class SyncRepo:
    def save(self, aggregate, command_id=None):
        return None

    def get_by_id(self, id_):
        return None

    def delete(self, id_):
        return None

    def pull_events(self):
        return []


print("sync repo isinstance Repository:", isinstance(SyncRepo(), Repository))


class PartialRepo:
    save = None
    get_by_id = None
    delete = None
    pull_events = None


print("attrs=None repo isinstance Repository:", isinstance(PartialRepo(), Repository))
print("Repository[Order, str] accepted at runtime:", Repository[Order, str] is not None)

print("--- DDD-17 ---")
code = (
    "import sys; import pydomain.ddd; "
    "print('es loaded:', 'pydomain.es' in sys.modules, '| infrastructure loaded:', 'pydomain.infrastructure' in sys.modules, "
    "'| cqrs loaded:', 'pydomain.cqrs' in sys.modules, '| modules:', len([m for m in sys.modules if m.startswith('pydomain')]))"
)
print(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip())


async def repo_probe() -> None:
    print("--- DDD-19 ---")
    repo: FakeRepository[Order, UUID] = FakeRepository()
    o = Order()
    await repo.save(o)
    print("version after insert:", o.version)
    await repo.save(o)
    print("version after no-op update:", o.version)
    await repo.save(o)
    print("version after second no-op update:", o.version)
    # concurrency detection when two handlers load "the same" aggregate
    h1 = await repo.get_by_id(o.id)
    h2 = await repo.get_by_id(o.id)
    print("two loads return same object:", h1 is h2)
    h1.total = 10
    await repo.save(h1)
    h2.total = 20
    try:
        await repo.save(h2)
        print("stale second writer NOT detected; stored total:", (await repo.get_by_id(o.id)).total)
    except ConcurrencyError as e:
        print("conflict detected:", e)

    print("--- DDD-05 consequence in FakeRepository ---")
    repo2: FakeRepository[Order, UUID] = FakeRepository()
    payload = {"total": 1}
    first = Order.model_validate(payload)
    payload["total"] = 2
    second = Order.model_validate(payload)
    await repo2.save(first)
    await repo2.save(second)
    stored = await repo2.get_by_id(first.id)
    print("same id:", first.id == second.id, "| first silently overwritten, stored total:", stored.total)


asyncio.run(repo_probe())
