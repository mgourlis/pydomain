"""Run the ``EventStore`` contract suite against ``FakeEventStore``."""

from __future__ import annotations

from typing import Any

import pytest

from pydomain.es.exceptions import DuplicateCommandError
from pydomain.testing import FakeEventStore
from pydomain.testing.contracts import EventStoreContract


class TestFakeEventStoreContract(EventStoreContract):
    @pytest.fixture
    def event_store(self) -> FakeEventStore:
        return FakeEventStore()

    @pytest.mark.xfail(
        strict=True,
        raises=pytest.fail.Exception,
        reason="DCE-124 ES-04: an empty append creates an empty stream",
    )
    @pytest.mark.anyio
    async def test_empty_append_creates_no_stream(self, event_store: Any) -> None:
        await super().test_empty_append_creates_no_stream(event_store)

    @pytest.mark.xfail(
        strict=True,
        raises=DuplicateCommandError,
        reason="DCE-124 ES-04: an empty append records its command_id",
    )
    @pytest.mark.anyio
    async def test_empty_append_does_not_record_command_id(
        self, event_store: Any
    ) -> None:
        await super().test_empty_append_does_not_record_command_id(event_store)
