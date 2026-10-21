"""Stateful test of the outbox and its relays (ADR-063, ADR-065, ADR-074).

Hypothesis drives a ``FakeOutboxStore`` shared by two ``OutboundEventGateway``
relays through random sequences of appends, relay passes, broker failures,
store failures, cancellations and clock moves, and checks after every step that:

- no entry is both published and dead-lettered, even when a relay's late
  failure arrives after another relay published the entry (B-71);
- the store marks published only entries the broker accepted;
- no entry's attempts exceed ``max_attempts``, plus one for each relay that
  redelivered it through a stale claim (B-80);
- the broker accepts an entry again only after a crash between its own publish
  and its own mark, or after a publish outlasted its claim (the at-least-once
  windows ADR-063 and ADR-074 accept), never because a different entry in the
  batch failed;
- the backoff of each entry never shrinks and never exceeds ``max_delay``.

A relay can also crash right after a fetch, leaving its claims to go stale, and a
relay's publish can hang while the clock passes ``claim_timeout`` and the other
relay runs. A targeted rule does this for an entry on its last attempt, so the
late-failure-after-takeover paths of B-71 and B-79 are common even under the
derandomized CI profile. At the end the faults stop and the clock runs on past
every backoff and claim: every entry is then published or dead-lettered.
"""

from __future__ import annotations

import asyncio
import contextlib
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
        self.last_fetch: list[str] = []

    async def fetch_unpublished(self, limit: int = 100) -> list[OutboxEntry]:
        entries = await super().fetch_unpublished(limit)
        self.last_fetch = [entry.message_id for entry in entries]
        return entries

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
        self.hold_next = False
        self.hold_id: str | None = None
        self.held_fails = False
        self.held = asyncio.Event()
        self.gate = asyncio.Event()

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        if self.hold_next or event.event_id == self.hold_id:
            self.hold_next = False
            self.hold_id = None
            self.held.set()
            await self.gate.wait()
            if self.held_fails:
                msg = "synthetic late broker failure"
                raise RuntimeError(msg)
            self.accepted[event.event_id] += 1
            return
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
        self.other_gateway = OutboundEventGateway(
            self.store,
            self.broker,
            _registry(),
            batch_size=3,
            base_delay=_BASE_DELAY,
            max_delay=_MAX_DELAY,
            max_attempts=_MAX_ATTEMPTS,
            clock=self.clock,
        )
        self.wide_gateway, self.other_wide_gateway = (
            OutboundEventGateway(
                self.store,
                self.broker,
                _registry(),
                batch_size=100,
                base_delay=_BASE_DELAY,
                max_delay=_MAX_DELAY,
                max_attempts=_MAX_ATTEMPTS,
                clock=self.clock,
            )
            for _ in range(2)
        )
        self.ids: list[str] = []
        self.stale_redeliveries: Counter[str] = Counter()

    def _run_pass(self, gateway: OutboundEventGateway | None = None) -> None:
        relay = gateway or self.gateway
        try:
            self.loop.run_until_complete(relay.run_once())
        except (_StoreFault, asyncio.CancelledError):
            pass
        finally:
            self.broker.reset_pass()

    # ── Rules ──────────────────────────────────────────────────────────

    def _new_entries(self, count: int) -> list[OutboxEntry]:
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
        return entries

    @rule(count=st.integers(min_value=1, max_value=3))
    def append(self, count: int) -> None:
        entries = self._new_entries(count)
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
    def second_relay_runs(self) -> None:
        self._run_pass(self.other_gateway)

    def _straddle(
        self,
        relay_a: OutboundEventGateway,
        relay_b: OutboundEventGateway,
        *,
        fails: bool,
    ) -> None:
        """Run *relay_a* until its held publish blocks, pass the claim timeout,
        run *relay_b*, then let A's publish finish (failing if *fails*)."""
        self.broker.held_fails = fails
        self.broker.held = asyncio.Event()
        self.broker.gate = asyncio.Event()
        pass_a = self.loop.create_task(relay_a.run_once())
        held = self.loop.create_task(self.broker.held.wait())
        self.loop.run_until_complete(
            asyncio.wait({pass_a, held}, return_when=asyncio.FIRST_COMPLETED)
        )
        self.broker.hold_next = False
        self.broker.hold_id = None
        if not pass_a.done():
            claimed_by_a = set(self.store.last_fetch)
            self.clock.now += _CLAIM_TIMEOUT + timedelta(seconds=1)
            self._run_pass(relay_b)
            # Only what B fetched from A's stale claim can be attempted twice.
            self.stale_redeliveries.update(claimed_by_a & set(self.store.last_fetch))
            self.broker.gate.set()
        held.cancel()
        try:
            self.loop.run_until_complete(pass_a)
        except (_StoreFault, asyncio.CancelledError):
            pass
        with contextlib.suppress(asyncio.CancelledError):
            self.loop.run_until_complete(held)
        self.broker.reset_pass()

    @rule(fails=st.booleans())
    def publish_outlasts_its_claim(self, fails: bool) -> None:
        """Relay A's publish hangs past its claim; relay B runs; then A resumes.

        Relay B may deliver what A had claimed, so those entries may be
        accepted twice (ADR-074). If A's publish then fails, its marks must
        not undo B's publication (B-71).
        """
        self.broker.hold_next = True
        self._straddle(self.gateway, self.other_gateway, fails=fails)

    @rule(published_first=st.booleans())
    def late_outcome_after_a_takeover(self, published_first: bool) -> None:
        """An entry on its last attempt is taken over by a second relay.

        With ``published_first``, B publishes it and A's held publish then
        fails on the last attempt: A's dead letter must be a no-op (B-71).
        Otherwise B's delivery fails on the last attempt and dead-letters it,
        and A's held publish then succeeds: the publication must win (B-79).
        """
        self.store.armed = set()
        (entry,) = self._new_entries(1)
        self.loop.run_until_complete(self.store.append([entry]))
        self.ids.append(entry.message_id)
        for _ in range(_MAX_ATTEMPTS - 1):
            self.loop.run_until_complete(
                self.store.mark_failed(
                    entry.message_id, error="synthetic", next_attempt_at=self.clock.now
                )
            )
        if not published_first:
            self.broker.failing.add(entry.message_id)
        self.broker.hold_id = entry.message_id
        self._straddle(
            self.wide_gateway, self.other_wide_gateway, fails=published_first
        )
        self.broker.failing.discard(entry.message_id)

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
        # A claim that went stale lets two relays attempt the same entry at
        # once, so each such overlap may add one attempt (ADR-074).
        for message_id in self.ids:
            allowed = _MAX_ATTEMPTS + self.stale_redeliveries[message_id]
            assert self.store.attempts.get(message_id, 0) <= allowed

    @invariant()
    def published_only_what_the_broker_accepted(self) -> None:
        assert set(self.store.published) <= set(self.broker.accepted)

    @invariant()
    def republished_only_after_its_own_mark_was_lost_or_its_claim_expired(
        self,
    ) -> None:
        for message_id, accepted in self.broker.accepted.items():
            allowed = (
                1
                + self.store.lost_marks[message_id]
                + self.stale_redeliveries[message_id]
            )
            assert accepted <= allowed, message_id

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


def test_publication_after_a_dead_letter_wins() -> None:
    """Replays the shortest sequence Hypothesis found for B-79.

    Relay B dead-letters an entry whose claim relay A let go stale; A's
    publish then succeeds, so the entry must end published, not both.
    """
    machine = OutboxRelayMachine()
    try:
        machine.append(count=1)
        machine.broker.failing.add(machine.ids[0])
        machine.publish_outlasts_its_claim(fails=True)
        machine.advance_clock(seconds=1)
        machine.publish_outlasts_its_claim(fails=False)

        machine.never_published_and_dead_lettered()
    finally:
        machine.loop.close()
