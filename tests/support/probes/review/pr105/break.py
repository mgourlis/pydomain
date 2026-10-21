import asyncio, traceback
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
    mode: str = "ok"

def tags(evs): return [e.tag for e in evs if isinstance(e, Issued)]

async def case_cancel():
    repo = FakeRepository(); rb = []
    class U(FakeUnitOfWork):
        async def rollback(self):
            rb.append(1); await super().rollback()
    async def h(cmd, uow):
        await repo.save(Inv.issue(cmd.tag))
        raise asyncio.CancelledError()
    bus = CommandBus(); bus.register(Do, h, uow_factory=lambda: U(repository=repo))
    try:
        await bus.dispatch(Do(tag="x"))
    except BaseException as e:
        print("1 cancel in handler ->", type(e).__name__, "rollbacks", len(rb), "buffer", tags(repo.pull_events()))

async def case_cancel_rollback_raises():
    repo = FakeRepository()
    class U(FakeUnitOfWork):
        async def rollback(self):
            raise RuntimeError("rb failed")
    async def h(cmd, uow):
        raise asyncio.CancelledError()
    bus = CommandBus(); bus.register(Do, h, uow_factory=lambda: U(repository=repo))
    try:
        await bus.dispatch(Do(tag="x"))
    except BaseException as e:
        print("2 cancel + rollback raises ->", type(e).__name__, "context", type(e.__context__).__name__)

async def case_commit_then_save():
    repo = FakeRepository()
    async def h(cmd, uow):
        if cmd.mode == "bad":
            await repo.save(Inv.issue("A")); await uow.commit(); await repo.save(Inv.issue("B-after-commit"))
        else:
            await repo.save(Inv.issue(cmd.tag))
        return EmptyCommandResult()
    bus = CommandBus(); bus.register(Do, h, uow_factory=lambda: FakeUnitOfWork(repository=repo))
    try: await bus.dispatch(Do(tag="x", mode="bad"))
    except CommandExecutionError as e: print("3 handler commits:", type(e.__cause__).__name__)
    c = Do(tag="next")
    _, evs = await bus.dispatch(c)
    print("3 next dispatch publishes", tags(evs), "all stamped with next cmd:", all(e.correlation_id == c.command_id for e in evs))

async def case_direct_commit_then_stage():
    repo = FakeRepository(); uow = FakeUnitOfWork(repository=repo)
    async with uow:
        await repo.save(Inv.issue("committed")); await uow.commit()
        await repo.save(Inv.issue("staged-after-commit"))
    async with uow:
        await uow.commit()
    print("4 direct: next scope collects", tags(uow.collect_events()))

async def case_session_rollback_raises_shared_repo():
    repo = FakeRepository(); n = {"c": 0}
    class U(FakeUnitOfWork):
        async def rollback(self):
            n["c"] += 1
            if n["c"] == 1: raise RuntimeError("connection lost")
            await super().rollback()
    async def h(cmd, uow):
        await repo.save(Inv.issue(cmd.tag))
        if cmd.mode == "refuse": raise ValueError("refused")
        return EmptyCommandResult()
    bus = CommandBus(); bus.register(Do, h, uow_factory=lambda: U(repository=repo))
    try: await bus.dispatch(Do(tag="refused", mode="refuse"))
    except CommandExecutionError as e: print("5 storage rollback raised; cause", type(e.__cause__).__name__, "context", type(e.__context__).__name__)
    _, evs = await bus.dispatch(Do(tag="next"))
    print("5 next dispatch publishes", tags(evs))

async def case_pull_events_raises():
    class Bad(FakeRepository):
        def pull_events(self): raise RuntimeError("pull failed")
    good = FakeRepository(); bad = Bad()
    uow = FakeUnitOfWork(repositories={"bad": bad, "good": good})
    await good.save(Inv.issue("refused"))
    try:
        async with uow:
            raise ValueError("body")
    except Exception as e:
        print("6 pull_events raises ->", type(e).__name__, e, "context", type(e.__context__).__name__)
    print("6 good repo still buffered:", tags(good.pull_events()))

async def case_traceback():
    class U(FakeUnitOfWork):
        async def rollback(self): raise RuntimeError("ROLLBACK-FAILED-MARKER")
    async def h(cmd, uow): raise ValueError("HANDLER-MARKER")
    bus = CommandBus(); bus.register(Do, h, uow_factory=U)
    try: await bus.dispatch(Do(tag="x"))
    except CommandExecutionError as e:
        txt = "".join(traceback.format_exception(e))
        print("7 traceback shows handler error:", "HANDLER-MARKER" in txt, "| shows rollback error:", "ROLLBACK-FAILED-MARKER" in txt, "| suppress_context:", e.__suppress_context__)

async def case_suppress():
    class U(FakeUnitOfWork):
        async def __aexit__(self, *a):
            await super().__aexit__(*a); return True
    async def h(cmd, uow): raise ValueError("x")
    bus = CommandBus(); bus.register(Do, h, uow_factory=U)
    try: print("8", await bus.dispatch(Do(tag="x")))
    except BaseException as e: print("8 suppressing aexit ->", type(e).__name__, e)

async def case_handler_rollback_then_raise_once():
    rb = []
    class U(FakeUnitOfWork):
        async def rollback(self): rb.append(1); await super().rollback()
    async def h(cmd, uow):
        await uow.rollback(); raise ValueError("x")
    bus = CommandBus(); bus.register(Do, h, uow_factory=U)
    try: await bus.dispatch(Do(tag="x"))
    except CommandExecutionError: print("9 handler rollback + raise: rollbacks", len(rb))

async def main():
    for c in [case_cancel, case_cancel_rollback_raises, case_commit_then_save, case_direct_commit_then_stage,
              case_session_rollback_raises_shared_repo, case_pull_events_raises, case_traceback, case_suppress,
              case_handler_rollback_then_raise_once]:
        try: await c()
        except BaseException as e: print(c.__name__, "PROBE CRASH", type(e).__name__, e)
asyncio.run(main())
