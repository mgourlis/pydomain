"""Run the ``LockProvider`` contract suite against ``FakeLockProvider``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeLockProvider
from pydomain.testing.contracts import LockProviderContract


class TestFakeLockProviderContract(LockProviderContract):
    @pytest.fixture
    def lock_provider(self) -> FakeLockProvider:
        return FakeLockProvider()

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-123 B-68: a late release frees the next holder's lock",
    )
    @pytest.mark.anyio
    async def test_late_release_after_another_holder_took_the_key_does_not_free_it(
        self, lock_provider: FakeLockProvider
    ) -> None:
        case = (
            super().test_late_release_after_another_holder_took_the_key_does_not_free_it
        )
        await case(lock_provider)
