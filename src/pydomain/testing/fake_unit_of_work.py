"""In-memory Unit of Work for testing."""

from __future__ import annotations

from typing import Any

from pydomain.cqrs.unit_of_work import AbstractUnitOfWork


class FakeUnitOfWork(AbstractUnitOfWork):
    """In-memory Unit of Work for testing.

    Stores the provided repository (or repositories) in ``_repos`` so
    that the base class ``_collect_and_stamp()`` can pull events from
    them. ``_flush()`` and ``_commit()`` are no-ops because
    ``FakeRepository`` is in-memory and requires no explicit
    persistence step.

    ``_rolled_back`` records whether the current scope rolled back; it is
    reset on each ``async with``. A rollback (explicit, on an exception, or
    on leaving without ``commit()``) drains the repositories' event buffers,
    so a repository shared across units of work never hands a failed
    scope's events to a later commit. It does not undo the saves
    themselves: ``FakeRepository`` keeps what was saved.

    Parameters
    ----------
    repository:
        A single repository stored under the ``"default"`` key.
    repositories:
        A dict of named repositories merged into ``_repos``.
        Useful when a handler needs access to multiple repositories.
    """

    def __init__(
        self,
        repository: Any | None = None,
        repositories: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        if repository is not None:
            self._repos["default"] = repository
        if repositories is not None:
            self._repos.update(repositories)
        self._rolled_back = False

    async def __aenter__(self) -> AbstractUnitOfWork:
        """Start a scope; ``_rolled_back`` reports only this scope's rollback."""
        self._rolled_back = False
        return await super().__aenter__()

    async def _flush(self) -> None:
        """No-op for in-memory testing."""
        pass

    async def rollback(self) -> None:
        """Record the rollback, then drain the repositories' event buffers."""
        self._rolled_back = True
        await super().rollback()
