"""Run the ``LockProvider`` contract suite against ``FakeLockProvider``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeLockProvider
from pydomain.testing.contracts import LockProviderContract


class TestFakeLockProviderContract(LockProviderContract):
    @pytest.fixture
    def lock_provider(self) -> FakeLockProvider:
        return FakeLockProvider()
