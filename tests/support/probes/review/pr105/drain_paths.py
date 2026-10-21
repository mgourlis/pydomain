import asyncio
from uuid import UUID, uuid4
from pydomain.cqrs import Command, CommandBus, CommandExecutionError, EmptyCommandResult
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository, FakeUnitOfWork

class Issued(DomainEvent):
    tag: str
class Inv(AggregateRoot[UUID]):
    @classmethod
    def issue(cls, tag):
        i = cls(id=uuid4()); i._add_event(Issued(tag=tag)); return i
class Do(Command[EmptyCommandResult]):
    tag: str
def tags(evs): return [e.tag for e in evs if isinstance(e, Issued)]

class Flaky(FakeRepository):
    """pull_events works for commit, raises on the next call."""
    calls = 0
    def pull_events(self):
        self.calls += 1
        if self.calls >= 2: raise RuntimeError("pull failed")
        return super().pull_events()

async def p1():
    repo = Flaky(); stored = {}
    async def h(cmd, uow):
        await repo.save(Inv.issue(cmd.tag)); return EmptyCommandResult()
    bus = CommandBus(); bus.register(Do, h, uow_factory=lambda: FakeUnitOfWork(repository=repo))
    try:
        r = await bus.dispatch(Do(tag="ok")); print("1 success + exit drain raises ->", tags(r[1]))
    except BaseException as e:
        print("1 success + exit drain raises -> caller gets", type(e).__name__, "(commit persisted:", len(repo._store) if hasattr(repo,'_store') else '?', ")")

async def p2():
    repo = Flaky()
    async def h(cmd, uow):
        await repo.save(Inv.issue(cmd.tag)); await uow.commit(); return EmptyCommandResult()
    bus = CommandBus(); bus.register(Do, h, uow_factory=lambda: FakeUnitOfWork(repository=repo))
    try: await bus.dispatch(Do(tag="x"))
    except CommandExecutionError as e:
        print("2 handler commits, exit drain raises -> notes", getattr(e, "__notes__", None), "cause", type(e.__cause__).__name__)

async def p3():
    repo = FakeRepository()
    class U(FakeUnitOfWork):
        async def _rollback(self): raise asyncio.CancelledError
    uow = U(repository=repo)
    await repo.save(Inv.issue("refused"))
    try:
        await uow.rollback()
    except BaseException as e:
        print("3 _rollback cancelled ->", type(e).__name__, "buffer", tags(repo.pull_events()))

async def p4():
    class Bad(FakeRepository):
        def pull_events(self): raise KeyError("drain")
    class U(FakeUnitOfWork):
        async def _rollback(self): raise RuntimeError("storage")
    uow = U(repositories={"b": Bad()})
    try: await uow.rollback()
    except BaseException as e:
        print("4 both raise ->", type(e).__name__, "context", type(e.__context__).__name__, "| next commit ok:", end=" ")
    try:
        await uow.commit(); print(True)
    except BaseException as e: print(type(e).__name__)

async def p5():
    # rollback() after a successful commit: hook runs on a committed transaction
    calls = []
    class U(FakeUnitOfWork):
        async def _rollback(self): calls.append("rollback")
        async def _commit(self): calls.append("commit")
    uow = U()
    async with uow:
        await uow.commit(); await uow.rollback()
    print("5 commit then rollback then exit ->", calls)

async def main():
    for p in (p1, p2, p3, p4, p5):
        try: await p()
        except BaseException as e: print(p.__name__, "CRASH", type(e).__name__, e)
asyncio.run(main())
