"""Contract suite for the ``ProcessedMessageStore`` Protocol (ADR-018)."""

from __future__ import annotations

import pytest

from pydomain.cqrs.idempotency import ProcessedMessageStore
from pydomain.testing.contracts._support import pending

__all__ = ["ProcessedMessageStoreContract"]


class ProcessedMessageStoreContract:
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

    def test_satisfies_protocol(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """The store is a ``ProcessedMessageStore``."""
        pending()

    @pytest.mark.anyio
    async def test_get_returns_missing_for_unknown_id(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """``get`` returns the ``MISSING`` sentinel for an unprocessed id."""
        pending()

    @pytest.mark.anyio
    async def test_get_returns_result_cached_by_set(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """``get`` returns the result ``set`` cached for the id."""
        pending()

    @pytest.mark.anyio
    async def test_cached_none_is_not_missing(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """A cached ``None`` comes back as ``None``, not ``MISSING`` (ADR-018)."""
        pending()

    @pytest.mark.anyio
    async def test_contains_is_false_for_unknown_id(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """``contains`` is ``False`` for an unprocessed id."""
        pending()

    @pytest.mark.anyio
    async def test_contains_is_true_after_set(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """An id with a cached result counts as processed."""
        pending()

    @pytest.mark.anyio
    async def test_check_and_set_is_false_the_first_time(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """The first ``check_and_set`` for an id reports it as new."""
        pending()

    @pytest.mark.anyio
    async def test_check_and_set_is_true_the_second_time(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """A repeated ``check_and_set`` reports the id as already processed."""
        pending()

    @pytest.mark.anyio
    async def test_check_and_set_marks_id_processed(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """After ``check_and_set``, ``contains`` reports the id as processed."""
        pending()

    @pytest.mark.anyio
    async def test_check_and_set_is_true_after_set(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """``set`` and ``check_and_set`` share one key space."""
        pending()

    @pytest.mark.anyio
    async def test_concurrent_check_and_set_admits_one_caller(
        self, processed_message_store: ProcessedMessageStore
    ) -> None:
        """Of concurrent ``check_and_set`` calls for one id, exactly one is first."""
        pending()
