"""Shared synthetic helpers for the ADR-076 doc-claim probes (no real data)."""
import asyncio
import logging

from pydomain.cqrs.behaviors import AggregateLockingBehavior, MessageContext


class Msg:
    def __init__(self, *keys):
        self.keys = list(keys)


class Resolver:
    def resolve(self, m):
        return m.keys


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, r):
        self.records.append((r.levelname, r.getMessage()))


def capture():
    c = Capture()
    lg = logging.getLogger("pydomain.pipeline")
    lg.addHandler(c)
    lg.setLevel(logging.DEBUG)
    return c


def behavior(provider):
    return AggregateLockingBehavior(provider, Resolver())


async def run(provider, msg, nxt):
    return await behavior(provider).handle(MessageContext(message=msg), nxt)
