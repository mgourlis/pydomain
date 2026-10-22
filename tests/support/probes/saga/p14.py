import asyncio
from uuid import uuid4
from common import *
from pydomain.infrastructure.message_bus import MessageBus

def billing():
    class OrderSaga(Saga[SagaState]):
        listens_to = [OrderCreated]
        def __init__(self, state):
            super().__init__(state)
            self.on(OrderCreated, send=lambda e: ChargePayment(order_id=e.order_id))
    return OrderSaga

def shipping():
    class OrderSaga(Saga[SagaState]):
        listens_to = [OrderCreated]
        def __init__(self, state):
            super().__init__(state)
            self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id))
    return OrderSaga

class PriorityOrderCreated(OrderCreated):
    pass

async def main():
    A, B = billing(), shipping()
    mgr, bus, repo = make_manager(A, B)
    await mgr.handle(OrderCreated(order_id="O", correlation_id=uuid4()))
    print(f"14a two classes named OrderSaga: rows={len(repo._store)} bus={bus.sent} type_map->{mgr.registry.get_saga_type('OrderSaga') is B and 'second class'}")
    mgr, bus, repo = make_manager(B)
    await mgr.handle(PriorityOrderCreated(order_id="P", correlation_id=uuid4()))
    print(f"14b event subclass: rows={len(repo._store)} bus={bus.sent}")
    mb = MessageBus()
    mgr, bus, repo = make_manager(B)
    calls = []
    orig = mgr.handle
    async def counting(ev):
        calls.append(1); await orig(ev)
    mgr.handle = counting
    mgr.bind_to(mb); mgr.bind_to(mb)
    await mb.dispatch(OrderCreated(order_id="Q", correlation_id=uuid4()))
    print(f"14c bind_to twice: manager.handle invoked {len(calls)}x; bus={bus.sent}")
asyncio.run(main())
