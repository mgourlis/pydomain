"""Run the ``ProcessedMessageStore`` contract suite against its fake."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeProcessedMessageStore
from pydomain.testing.contracts import ProcessedMessageStoreContract

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="DCE-110 contract cases not implemented yet",
)


class TestFakeProcessedMessageStoreContract(ProcessedMessageStoreContract):
    @pytest.fixture
    def processed_message_store(self) -> FakeProcessedMessageStore:
        return FakeProcessedMessageStore()
