"""Contract suite for the ``ProjectionStore`` Protocol (ADR-024, ADR-025)."""

from __future__ import annotations

import pytest

from pydomain.cqrs.projection import ProjectionStore

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
        assert isinstance(projection_store, ProjectionStore)

    @pytest.mark.anyio
    async def test_load_returns_none_for_unknown_projection(
        self, projection_store: ProjectionStore
    ) -> None:
        """A projection with no saved state loads as ``None``."""
        assert await projection_store.load("contract-unknown") is None

    @pytest.mark.anyio
    async def test_load_returns_saved_state(
        self, projection_store: ProjectionStore
    ) -> None:
        """``load`` returns the state ``save`` persisted."""
        state = {"order_count": 3, "total": "42.50"}

        await projection_store.save("contract-order-summary", state)

        assert await projection_store.load("contract-order-summary") == state

    @pytest.mark.anyio
    async def test_save_replaces_previous_state(
        self, projection_store: ProjectionStore
    ) -> None:
        """A second ``save`` for a projection replaces the first."""
        await projection_store.save("contract-order-summary", {"order_count": 1})
        await projection_store.save("contract-order-summary", {"order_count": 2})

        assert await projection_store.load("contract-order-summary") == {
            "order_count": 2
        }

    @pytest.mark.anyio
    async def test_states_are_kept_per_projection(
        self, projection_store: ProjectionStore
    ) -> None:
        """Each projection identity has its own state."""
        await projection_store.save("contract-orders", {"count": 1})
        await projection_store.save("contract-invoices", {"count": 2})

        assert await projection_store.load("contract-orders") == {"count": 1}
        assert await projection_store.load("contract-invoices") == {"count": 2}
