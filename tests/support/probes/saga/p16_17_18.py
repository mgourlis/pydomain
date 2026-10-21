import asyncio
from datetime import timedelta
from uuid import uuid4
from common import *
from pydomain.cqrs.saga.pruning import StepThresholdPruningPolicy

class CappedState(SagaState):
    max_processed_events = 3

class Counter(Saga[CappedState]):
    state_class = CappedState
    listens_to = [OrderCreated]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id))

async def s16():
    trials, evicted_latest = 200, 0
    for _ in range(trials):
        st = CappedState(id=uuid4(), saga_type="Counter")
        ids = [uuid4() for _ in range(5)]
        for i in ids: st.mark_event_processed(i)
        if ids[-1] not in st.processed_event_ids: evicted_latest += 1
    print(f"16 cap=3, 5 events: newest id evicted in {evicted_latest}/{trials} trials")
    # reprocess through the manager
    mgr, bus, repo = make_manager(Counter)
    cid = uuid4()
    evs = [OrderCreated(order_id=f"O{i}", correlation_id=cid) for i in range(5)]
    for e in evs: await mgr.handle(e)
    st = await only_state(repo)
    missing = [i for i, e in enumerate(evs) if e.event_id not in st.processed_event_ids]
    bus.sent.clear()
    for i in missing: await mgr.handle(evs[i])
    print(f"16 evicted event indexes={missing}; redelivering them dispatched {bus.sent}")
    # prune_history without guard
    st = SagaState(id=uuid4(), saga_type="X", status=SagaStatus.COMPLETED, processed_event_ids={uuid4(), uuid4()})
    st.prune_history(keep_last_n_steps=0, keep_last_n_events=0)
    print(f"16 prune_history on COMPLETED: events left={len(st.processed_event_ids)} (no guard)")

class S17(Saga[SagaState]):
    listens_to = []

def s17():
    import pydomain.cqrs.saga.exceptions as ex
    st = SagaState(id=uuid4(), saga_type="S17", status=SagaStatus.COMPENSATED)
    s = S17(st); s.complete(); a = st.status
    st2 = SagaState(id=uuid4(), saga_type="S17", status=SagaStatus.FAILED); S17(st2).suspend("x"); b = st2.status
    st3 = SagaState(id=uuid4(), saga_type="S17", status=SagaStatus.COMPLETED)
    asyncio.run(S17(st3).execute_compensations()); c = st3.status
    print(f"17 COMPENSATED.complete()->{a}; FAILED.suspend()->{b}; COMPLETED.execute_compensations()->{c}; no SagaStateError raised")

class Gate(Saga[SagaState]):
    listens_to = [OrderCreated, ApprovalGranted, PaymentDeclined]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id), step="awaiting", suspend=True, suspend_timeout=None)
        self.on(ApprovalGranted, send=lambda e: ConfirmOrder(order_id=e.order_id), resumes_from="awaiting", complete=True)
        # resumes_from omitted -> documented as "None means unrestricted (any step)"
        self.on(PaymentDeclined, send=lambda e: NotifyCustomer(order_id=e.order_id), fail=True)

async def s18():
    mgr, bus, repo = make_manager(Gate)
    cid = uuid4()
    await mgr.handle(OrderCreated(order_id="O", correlation_id=cid))
    await mgr.handle(PaymentDeclined(order_id="O", correlation_id=cid))
    st = await only_state(repo)
    print(f"18 PaymentDeclined (resumes_from=None) while suspended: status={st.status} bus={bus.sent}")

asyncio.run(s16()); s17(); asyncio.run(s18())
