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
    refuse: bool = False
def tags(evs): return [e.tag for e in evs if isinstance(e, Issued)]

class SlowCommitUoW(FakeUnitOfWork):
    gate: asyncio.Event | None = None
    async def _commit(self):
        if self.gate is not None:
            await self.gate.wait()

async def run(name, a_refuse, a_gate):
    repo = FakeRepository()
    b_saved = asyncio.Event(); a_saved = asyncio.Event(); gate = asyncio.Event()
    async def h(cmd, uow):
        await repo.save(Inv.issue(cmd.tag))
        if cmd.tag == "A":
            a_saved.set()
            if not a_gate:
                await b_saved.wait()
            if cmd.refuse: raise ValueError("refused")
        else:
            await a_saved.wait()
            b_saved.set()
            if a_gate:
                gate.set()  # let A's _commit finish after B saved
            await asyncio.sleep(0); await asyncio.sleep(0); await asyncio.sleep(0)
        return EmptyCommandResult()
    def factory_a():
        u = SlowCommitUoW(repository=repo); u.gate = gate if a_gate else None; return u
    bus_a = CommandBus(); bus_a.register(Do, h, uow_factory=factory_a)
    bus_b = CommandBus(); bus_b.register(Do, h, uow_factory=lambda: FakeUnitOfWork(repository=repo))
    async def da():
        try: _, e = await bus_a.dispatch(Do(tag="A", refuse=a_refuse)); return tags(e)
        except CommandExecutionError: return "failed"
    async def db():
        await asyncio.sleep(0)
        _, e = await bus_b.dispatch(Do(tag="B")); return tags(e)
    ra, rb = await asyncio.gather(da(), db())
    print(f"{name}: A -> {ra}  B -> {rb}  left in buffer {tags(repo.pull_events())}")

async def main():
    await run("i  A fails after B saved", True, False)
    await run("ii A commits, B saves during A's _commit", False, True)
    await run("iii both save, A commits first", False, False)
asyncio.run(main())
