"""Run the ``OutboxStore`` contract suite against ``FakeOutboxStore``."""

from __future__ import annotations

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
        reason="DCE-148 FAKE-05: the fake's append accepts a duplicate message_id",
    )
    @pytest.mark.anyio
    async def test_append_refuses_a_duplicate_message_id(
        self, outbox_store: FakeOutboxStore
    ) -> None:
        await super().test_append_refuses_a_duplicate_message_id(outbox_store)
