"""Fake-specific tests for ``InMemoryMessageSubscriber``.

The ``MessageSubscriber`` Protocol cases run in
``test_contracts_message_subscriber.py``; these cover what only the fake
promises, such as ``simulate_message()``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pydomain.testing import InMemoryMessageSubscriber


class TestSimulateMessage:
    @pytest.mark.anyio
    async def test_unknown_topic_raises_key_error(self) -> None:
        """A message on a topic with no handler raises ``KeyError`` (A28)."""
        subscriber = InMemoryMessageSubscriber()

        with pytest.raises(KeyError, match="orders.placed"):
            await subscriber.simulate_message("orders.placed", {"order_id": "o-1"})


class TestLifecycleFlags:
    """``started`` and ``stopped`` record the lifecycle calls for assertions."""

    def test_new_subscriber_is_neither_started_nor_stopped(self) -> None:
        subscriber = InMemoryMessageSubscriber()

        assert subscriber.started is False
        assert subscriber.stopped is False

    @pytest.mark.anyio
    async def test_start_sets_started_only(self) -> None:
        subscriber = InMemoryMessageSubscriber()

        await subscriber.start()

        assert subscriber.started is True
        assert subscriber.stopped is False

    @pytest.mark.anyio
    async def test_stop_sets_stopped(self) -> None:
        subscriber = InMemoryMessageSubscriber()

        await subscriber.stop()

        assert subscriber.stopped is True


class _Refused(Exception):
    """A handler refuses the message."""


class TestOutcomes:
    """DCE-152 (B-190, FAKE-09): ``outcomes`` records each delivery's ACK or
    NACK, and the exception is still re-raised."""

    @pytest.mark.anyio
    async def test_outcomes_record_ack_and_nack_with_the_exception_type(
        self,
    ) -> None:
        subscriber = InMemoryMessageSubscriber()

        async def accept(payload: dict[str, Any]) -> None:
            pass

        async def refuse(payload: dict[str, Any]) -> None:
            raise _Refused("synthetic refusal")

        subscriber.subscribe("shipments.dispatched", accept)
        subscriber.subscribe("shipments.held", refuse)

        await subscriber.simulate_message("shipments.dispatched", {"n": 1})
        with pytest.raises(_Refused):
            await subscriber.simulate_message("shipments.held", {"n": 2})
        await subscriber.simulate_message("shipments.dispatched", {"n": 3})

        assert subscriber.outcomes == ["ack", "nack _Refused", "ack"]

    @pytest.mark.anyio
    async def test_failing_delivery_is_still_reraised(self) -> None:
        """(guard) The handler's exception reaches the caller, unchanged."""
        subscriber = InMemoryMessageSubscriber()
        refusal = _Refused("synthetic refusal")

        async def refuse(payload: dict[str, Any]) -> None:
            raise refusal

        subscriber.subscribe("shipments.held", refuse)

        with pytest.raises(_Refused) as raised:
            await subscriber.simulate_message("shipments.held", {"n": 1})

        assert raised.value is refusal

    @pytest.mark.anyio
    async def test_unknown_topic_records_no_outcome(self) -> None:
        subscriber = InMemoryMessageSubscriber()

        with pytest.raises(KeyError):
            await subscriber.simulate_message("orders.placed", {"order_id": "o-1"})

        assert subscriber.outcomes == []

    @pytest.mark.anyio
    async def test_cancelled_delivery_records_no_outcome(self) -> None:
        """ADR-081 point 9: a ``BaseException`` records nothing."""
        subscriber = InMemoryMessageSubscriber()

        async def cancelled(payload: dict[str, Any]) -> None:
            raise asyncio.CancelledError

        subscriber.subscribe("shipments.held", cancelled)

        with pytest.raises(asyncio.CancelledError):
            await subscriber.simulate_message("shipments.held", {"n": 1})

        assert subscriber.outcomes == []
