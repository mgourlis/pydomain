"""Contract suite for the ``ProcessedMessageStore`` Protocol (ADR-018)."""

from __future__ import annotations

from uuid import UUID, uuid4

import anyio
import pytest

from pydomain.cqrs.idempotency import MISSING, ProcessedMessageStore
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["ProcessedMessageStoreContract"]


class ProcessedMessageStoreContract(AsyncioBackend):
    """Cases every :class:`~pydomain.cqrs.idempotency.ProcessedMessageStore` passes.

    Override the ``processed_message_store`` fixture to return a fresh, empty
    store for each test. The cases cache JSON-compatible ``dict`` results.
    """

    @pytest.fixture
    def processed_message_store(self) -> ProcessedMessageStore:
        """Return a fresh, empty store. Override in your subclass."""
        raise NotImplementedError(
            "Override the `processed_message_store` fixture to return your adapter."
        )

    @pytest.mark.anyio
    async def test_satisfies_protocol(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """The store is a ``ProcessedMessageStore``."""
        assert isinstance(processed_message_store, ProcessedMessageStore)

    @pytest.mark.anyio
    async def test_get_returns_missing_for_unknown_id(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """``get`` returns the ``MISSING`` sentinel for an unprocessed id."""
        assert await processed_message_store.get(uuid4()) is MISSING

    @pytest.mark.anyio
    async def test_get_returns_result_cached_by_set(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """``get`` returns the result ``set`` cached for the id."""
        message_id = uuid4()
        result = {"order_id": "order-1", "status": "placed"}

        await processed_message_store.set(message_id, result)

        assert await processed_message_store.get(message_id) == result

    @pytest.mark.anyio
    async def test_cached_none_is_not_missing(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """A cached ``None`` comes back as ``None``, not ``MISSING`` (ADR-018)."""
        message_id = uuid4()

        await processed_message_store.set(message_id, None)
        cached = await processed_message_store.get(message_id)

        assert cached is not MISSING
        assert cached is None

    @pytest.mark.anyio
    async def test_contains_is_false_for_unknown_id(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """``contains`` is ``False`` for an unprocessed id."""
        assert await processed_message_store.contains(uuid4()) is False

    @pytest.mark.anyio
    async def test_contains_is_true_after_set(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """An id with a cached result counts as processed."""
        message_id = uuid4()

        await processed_message_store.set(message_id, {"status": "placed"})

        assert await processed_message_store.contains(message_id) is True

    @pytest.mark.anyio
    async def test_check_and_set_is_false_the_first_time(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """The first ``check_and_set`` for an id reports it as new."""
        assert await processed_message_store.check_and_set(uuid4()) is False

    @pytest.mark.anyio
    async def test_check_and_set_is_true_the_second_time(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """A repeated ``check_and_set`` reports the id as already processed."""
        message_id = uuid4()

        await processed_message_store.check_and_set(message_id)

        assert await processed_message_store.check_and_set(message_id) is True

    @pytest.mark.anyio
    async def test_check_and_set_marks_id_processed(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """After ``check_and_set``, ``contains`` reports the id as processed."""
        message_id = uuid4()

        await processed_message_store.check_and_set(message_id)

        assert await processed_message_store.contains(message_id) is True

    @pytest.mark.anyio
    async def test_concurrent_check_and_set_admits_one_caller(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """Of concurrent ``check_and_set`` calls for one id, exactly one is first."""
        message_id = uuid4()
        firsts: list[bool] = []

        async def check(id_: UUID) -> None:
            already = await processed_message_store.check_and_set(id_)
            firsts.append(not already)

        with anyio.fail_after(10):
            async with anyio.create_task_group() as tg:
                for _ in range(10):
                    tg.start_soon(check, message_id)

        assert firsts.count(True) == 1
