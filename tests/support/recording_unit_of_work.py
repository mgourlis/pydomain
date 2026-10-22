"""A ``FakeUnitOfWork`` that records its storage hooks.

Tests that need to know whether a scope committed or rolled back read
``hooks`` instead of the unit of work's private state (TEST-02)::

    uow = RecordingUnitOfWork()
    bus.register(PlaceOrder, handler, uow_factory=lambda: uow)
    await bus.dispatch(PlaceOrder(...))
    assert uow.hooks == ["commit"]

``_commit`` and ``_rollback`` are the storage hooks ``AbstractUnitOfWork``
documents for adapters to override: ``_commit`` runs last in a commit that
succeeds, ``_rollback`` first in every rollback, so ``"commit"`` means the
scope committed and ``"rollback"`` that it rolled back.
"""

from __future__ import annotations

from typing import Any

from pydomain.testing import FakeUnitOfWork


class RecordingUnitOfWork(FakeUnitOfWork):
    """Append ``"commit"`` or ``"rollback"`` to ``hooks`` as each hook runs."""

    def __init__(
        self,
        repository: Any | None = None,
        repositories: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(repository=repository, repositories=repositories)
        self.hooks: list[str] = []

    async def _commit(self) -> None:
        self.hooks.append("commit")
        await super()._commit()

    async def _rollback(self) -> None:
        self.hooks.append("rollback")
        await super()._rollback()
