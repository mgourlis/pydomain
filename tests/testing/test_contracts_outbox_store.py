"""Run the ``OutboxStore`` contract suite against ``FakeOutboxStore``."""

from __future__ import annotations

from typing import Any

import pytest

from pydomain.testing import FakeOutboxStore
from pydomain.testing.contracts import OutboxStoreContract


class TestFakeOutboxStoreContract(OutboxStoreContract):
    @pytest.fixture
    def outbox_store(self) -> FakeOutboxStore:
        return FakeOutboxStore()

    @pytest.mark.xfail(
        strict=True,
        raises=pytest.fail.Exception,
        reason="DCE-122 FAKE-05: limit <= 0 returns every entry",
    )
    @pytest.mark.anyio
    @pytest.mark.parametrize("limit", [0, -1])
    async def test_fetch_with_non_positive_limit_raises_value_error(
        self, outbox_store: Any, limit: int
    ) -> None:
        await super().test_fetch_with_non_positive_limit_raises_value_error(
            outbox_store, limit
        )

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-122 FAKE-05: the fake has no claim, so a second fetch repeats",
    )
    @pytest.mark.anyio
    async def test_second_fetch_before_any_mark_returns_other_entries(
        self, outbox_store: Any
    ) -> None:
        await super().test_second_fetch_before_any_mark_returns_other_entries(
            outbox_store
        )
