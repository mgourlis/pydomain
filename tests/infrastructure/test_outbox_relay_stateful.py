"""Stateful test of the outbox and its relay (ADR-063, ADR-065, ADR-074).

Hypothesis drives a ``FakeOutboxStore`` and an ``OutboundEventGateway`` through
random sequences of appends, relay passes, broker failures, store failures,
cancellations and clock moves, and checks after every step that:

- no entry is both published and dead-lettered;
- no entry's attempts exceed ``max_attempts``;
- the broker accepts an entry again only after a crash between its own publish
  and its own mark (the at-least-once window ADR-063 accepts), never because a
  different entry in the batch failed;
- the backoff of each entry never shrinks and never exceeds ``max_delay``.

A relay can also crash right after a fetch, leaving its claims to go stale
(ADR-074). At the end the faults stop and the clock runs on past every backoff
and claim: every entry is then published or dead-lettered.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore

_MAX_ATTEMPTS = 3
_BASE_DELAY = timedelta(seconds=1)
_MAX_DELAY = timedelta(seconds=10)
_CLAIM_TIMEOUT = timedelta(seconds=60)
_TOPIC = "shipments.dispatched"


class ShipmentDispatched(DomainEvent):
    shipment_ref: str


class ShipmentDispatchedIntegration(IntegrationEvent):
    shipment_ref: str


def _registry() -> OutboundEventRegistry:
    registry = OutboundEventRegistry()
    registry.register_translation(
        ShipmentDispatched,
        ShipmentDispatchedIntegration,
        _TOPIC,
        lambda event: ShipmentDispatchedIntegration(shipment_ref="synthetic"),
    )
    return registry


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class _StoreFault(Exception):
    """An injected outbox store failure."""


class _FaultyStore(FakeOutboxStore):
    """``FakeOutboxStore`` whose next mark call of one kind can be made to raise."""

    def __init__(self, clock: _Clock) -> None:
        super().__init__(clock=clock, claim_timeout=_CLAIM_TIMEOUT)
        self._clock_source = clock
        self.armed: set[str] = set()
        self.lost_marks: Counter[str] = Counter()
        self.delays: dict[str, list[timedelta]] = {}

    def _fire(self, method: str) -> bool:
        if method in self.armed:
            self.armed = set()
            return True
        return False

    async def mark_published(self, message_ids: Sequence[str]) -> None:
        if self._fire("mark_published"):
            self.lost_marks.update(message_ids)
            raise _StoreFault
        await super().mark_published(message_ids)

    async def mark_failed(
        self, message_id: str, *, error: str, next_attempt_at: datetime
    ) -> None:
        if self._fire("mark_failed"):
            raise _StoreFault
        self.delays.setdefault(message_id, []).append(
            next_attempt_at - self._clock_source()
        )
        await super().mark_failed(
            message_id, error=error, next_attempt_at=next_attempt_at
        )

    async def mark_dead_lettered(self, message_id: str, *, error: str) -> None:
        if self._fire("mark_dead_lettered"):
            raise _StoreFault
        await super().mark_dead_lettered(message_id, error=error)


class _ScriptedBroker:
    """Broker that fails for chosen ids, or at the Nth publish of a pass."""

    def __init__(self) -> None:
        self.failing: set[str] = set()
        self.fail_at: int | None = None
        self.cancel_at: int | None = None
        self.calls = 0
        self.accepted: Counter[str] = Counter()

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        self.calls += 1
        if self.calls == self.cancel_at:
            raise asyncio.CancelledError
        if self.calls == self.fail_at or event.event_id in self.failing:
            msg = "synthetic broker failure"
            raise RuntimeError(msg)
        self.accepted[event.event_id] += 1

    def reset_pass(self) -> None:
        """Forget the per-pass script."""
        self.calls = 0
        self.fail_at = None
        self.cancel_at = None

    async def start(self) -> None:
        """No-op."""

    async def stop(self) -> None:
        """No-op."""


class OutboxRelayMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.clock = _Clock()
        self.store = _FaultyStore(self.clock)
        self.broker = _ScriptedBroker()
        self.gateway = OutboundEventGateway(
            self.store,
            self.broker,
            _registry(),
            batch_size=2,
            base_delay=_BASE_DELAY,
            max_delay=_MAX_DELAY,
            max_attempts=_MAX_ATTEMPTS,
            clock=self.clock,
        )
        self.ids: list[str] = []

    def _run_pass(self) -> None:
        try:
            self.loop.run_until_complete(self.gateway.run_once())
        except (_StoreFault, asyncio.CancelledError):
            pass
        finally:
            self.broker.reset_pass()

    # ── Rules ──────────────────────────────────────────────────────────

    @rule(count=st.integers(min_value=1, max_value=3))
    def append(self, count: int) -> None:
        entries = []
        for _ in range(count):
            event = ShipmentDispatchedIntegration(shipment_ref="synthetic")
            entries.append(
                OutboxEntry(
                    message_id=event.event_id,
                    topic=_TOPIC,
                    payload=event.model_dump(),
                    occurred_at=self.clock.now,
                )
            )
        self.loop.run_until_complete(self.store.append(entries))
        self.ids.extend(entry.message_id for entry in entries)

    @precondition(lambda self: bool(self.ids))
    @rule(data=st.data())
    def broker_fails_for(self, data: st.DataObject) -> None:
        self.broker.failing.add(data.draw(st.sampled_from(self.ids)))

    @precondition(lambda self: bool(self.broker.failing))
    @rule(data=st.data())
    def broker_recovers_for(self, data: st.DataObject) -> None:
        self.broker.failing.discard(
            data.draw(st.sampled_from(sorted(self.broker.failing)))
        )

    @rule(
        method=st.sampled_from(["mark_published", "mark_failed", "mark_dead_lettered"])
    )
    def store_fails_next(self, method: str) -> None:
        self.store.armed = {method}

    @rule(position=st.integers(min_value=1, max_value=2), cancel=st.booleans())
    def crash_mid_batch(self, position: int, cancel: bool) -> None:
        """Run a pass whose *position*-th delivery crashes the pass.

        Either the publish is cancelled, or it fails and recording the failure
        raises.
        """
        if cancel:
            self.broker.cancel_at = position
        else:
            self.broker.fail_at = position
            self.store.armed = {"mark_failed", "mark_dead_lettered"}
        self._run_pass()
        self.store.armed = set()

    @rule()
    def run_once(self) -> None:
        self._run_pass()

    @rule()
    def relay_crashes_after_fetch(self) -> None:
        """A relay claims a batch and dies before delivering it."""
        self.loop.run_until_complete(self.store.fetch_unpublished(2))

    @rule(seconds=st.integers(min_value=0, max_value=90))
    def advance_clock(self, seconds: int) -> None:
        self.clock.now += timedelta(seconds=seconds)

    # ── Invariants ─────────────────────────────────────────────────────

    @invariant()
    def never_published_and_dead_lettered(self) -> None:
        for message_id in self.ids:
            assert not (
                self.store.is_published(message_id)
                and self.store.is_dead_lettered(message_id)
            ), message_id

    @invariant()
    def attempts_never_exceed_the_budget(self) -> None:
        for message_id in self.ids:
            assert self.store.attempts.get(message_id, 0) <= _MAX_ATTEMPTS

    @invariant()
    def republished_only_after_its_own_mark_was_lost(self) -> None:
        for message_id, accepted in self.broker.accepted.items():
            assert accepted <= 1 + self.store.lost_marks[message_id], message_id

    @invariant()
    def backoff_is_monotone_and_capped(self) -> None:
        for delays in self.store.delays.values():
            assert delays == sorted(delays)
            assert all(delay <= _MAX_DELAY for delay in delays)

    # ── Drain ──────────────────────────────────────────────────────────

    def teardown(self) -> None:
        try:
            self.store.armed = set()
            self.broker.failing.clear()
            for _ in range(len(self.ids) + _MAX_ATTEMPTS + 2):
                self.clock.now += max(_MAX_DELAY, _CLAIM_TIMEOUT) + timedelta(seconds=1)
                self._run_pass()
            for message_id in self.ids:
                assert self.store.is_published(
                    message_id
                ) or self.store.is_dead_lettered(message_id), message_id
        finally:
            self.loop.close()


def test_outbox_relay_state_machine() -> None:
    run_state_machine_as_test(
        OutboxRelayMachine,
        settings=settings(
            max_examples=200,
            stateful_step_count=30,
            report_multiple_bugs=False,
            deadline=None,
        ),
    )
