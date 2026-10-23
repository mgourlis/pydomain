"""Run the ``ProcessedMessageStore`` contract suite against its fake."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeProcessedMessageStore
from pydomain.testing.contracts import ProcessedMessageStoreContract


class TestFakeProcessedMessageStoreContract(ProcessedMessageStoreContract):
    @pytest.fixture
    def processed_message_store(self) -> FakeProcessedMessageStore:
        return FakeProcessedMessageStore()

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-148 FAKE-06: the fake's check_and_set stores a result",
    )
    @pytest.mark.anyio
    async def test_check_and_set_creates_no_result(
        self, processed_message_store: FakeProcessedMessageStore
    ) -> None:
        await super().test_check_and_set_creates_no_result(processed_message_store)
