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
        raises=DuplicateCommandError,
        reason="DCE-124 ES-03: command_id recorded before the version check",
    )
    @pytest.mark.anyio
    async def test_retry_after_concurrency_error_with_same_command_id_succeeds(
        self, event_store: Any
    ) -> None:
        await super().test_retry_after_concurrency_error_with_same_command_id_succeeds(
            event_store
        )

    @pytest.mark.xfail(
        strict=True,
        raises=pytest.fail.Exception,
        reason="DCE-124 ES-05: a negative from_version slices from the end",
    )
    @pytest.mark.anyio
    async def test_negative_from_version_raises_value_error(
        self, event_store: Any
    ) -> None:
        await super().test_negative_from_version_raises_value_error(event_store)
