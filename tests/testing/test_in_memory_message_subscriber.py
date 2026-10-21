"""Fake-specific tests for ``InMemoryMessageSubscriber``.

The ``MessageSubscriber`` Protocol cases run in
``test_contracts_message_subscriber.py``; these cover what only the fake
promises, such as ``simulate_message()``.
"""

from __future__ import annotations

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
