"""The async backend every contract suite runs on unless a subclass chooses."""

from __future__ import annotations

import pytest

__all__ = ["AsyncioBackend"]


class AsyncioBackend:
    """Pin the suite's ``anyio`` cases to asyncio.

    A ``conftest.py`` that parametrizes ``anyio_backend`` does not reach the
    suite. Override ``anyio_backend`` in your subclass to run it on another
    backend, or on several.

    The pin is session-scoped: ``anyio`` makes every async fixture depend on
    ``anyio_backend``, so its scope must be at least as wide as the widest
    async fixture your adapter fixture uses.
    """

    @pytest.fixture(scope="session")
    def anyio_backend(self) -> str:
        """Return ``"asyncio"``. Override in your subclass to change it."""
        return "asyncio"
