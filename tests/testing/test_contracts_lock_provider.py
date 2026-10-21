"""Run the ``LockProvider`` contract suite against ``FakeLockProvider``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeLockProvider
from pydomain.testing.contracts import LockProviderContract

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="DCE-110 contract cases not implemented yet",
)


class TestFakeLockProviderContract(LockProviderContract):
    @pytest.fixture
    def lock_provider(self) -> FakeLockProvider:
        return FakeLockProvider()
