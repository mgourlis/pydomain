import asyncio
from uuid import uuid4
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork
from pydomain.ddd.domain_event import DomainEvent

class Happened(DomainEvent):
    n: int

class Repo:
    def __init__(self): self.buf = []
    def pull_events(self):
        out, self.buf = self.buf, []
        return out

class Writer:
    def __init__(self): self.writes = []
    async def write(self, events): self.writes.append([e.n for e in events])

class UoW(AbstractUnitOfWork):
    def __init__(self, w):
        super().__init__(outbox_writer=w)
        self.repo = Repo(); self._repos = {"r": self.repo}
        self.commits = 0
    async def _commit(self): self.commits += 1

async def main():
    w = Writer(); uow = UoW(w)
    async with uow:
        uow.repo.buf.append(Happened(n=1))
        await uow.commit()          # e.g. a handler committing
        uow.repo.buf.append(Happened(n=2))
        await uow.commit()          # the CommandBus committing again
    print("outbox writes:", w.writes, "db commits:", uow.commits, "collected:", [e.n for e in uow.collect_events()])

asyncio.run(main())
