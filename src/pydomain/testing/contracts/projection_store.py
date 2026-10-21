"""Contract suite for the ``ProjectionStore`` Protocol (ADR-024, ADR-025)."""

from __future__ import annotations

import pytest

from pydomain.cqrs.projection import ProjectionStore
from pydomain.testing.contracts._support import pending

__all__ = ["ProjectionStoreContract"]


class ProjectionStoreContract:
    """Cases every :class:`~pydomain.cqrs.projection.ProjectionStore` passes.

    Override the ``projection_store`` fixture to return a fresh, empty store
    for each test. The cases store JSON-compatible ``dict`` states.
    """

    @pytest.fixture
    def projection_store(self) -> ProjectionStore:
        """Return a fresh, empty store. Override in your subclass."""
        raise NotImplementedError(
            "Override the `projection_store` fixture to return your adapter."
        )

    def test_satisfies_protocol(self, projection_store: ProjectionStore) -> None:
        """The store is a ``ProjectionStore``."""
        pending()

    @pytest.mark.anyio
    async def test_load_returns_none_for_unknown_projection(
        self, projection_store: ProjectionStore
    ) -> None:
        """A projection with no saved state loads as ``None``."""
        pending()

    @pytest.mark.anyio
    async def test_load_returns_saved_state(
        self, projection_store: ProjectionStore
    ) -> None:
        """``load`` returns the state ``save`` persisted."""
        pending()

    @pytest.mark.anyio
    async def test_save_replaces_previous_state(
        self, projection_store: ProjectionStore
    ) -> None:
        """A second ``save`` for a projection replaces the first."""
        pending()

    @pytest.mark.anyio
    async def test_states_are_kept_per_projection(
        self, projection_store: ProjectionStore
    ) -> None:
        """Each projection identity has its own state."""
        pending()
