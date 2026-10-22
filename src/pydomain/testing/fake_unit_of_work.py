"""In-memory Unit of Work for testing."""

from __future__ import annotations

from typing import Any, Literal

from pydomain.cqrs.unit_of_work import AbstractUnitOfWork

_FailAt = Literal["flush", "write_outbox", "commit"]
"""The ``commit()`` hooks that :class:`FakeUnitOfWork` can make fail."""


class FakeUnitOfWork(AbstractUnitOfWork):
    """In-memory Unit of Work for testing.

    Stores the provided repository (or repositories) in ``_repos`` so
    that the base class ``_collect_and_stamp()`` can pull events from
    them. ``_flush()``, ``_write_outbox()`` and ``_commit()`` do nothing
    unless ``fail_at`` names them, because ``FakeRepository`` is in-memory
    and requires no explicit persistence step.

    ``rolled_back`` (read-only) records whether the current scope rolled
    back; it is reset on each ``async with``. The private ``_rolled_back``
    it reads keeps working for existing tests. A rollback (explicit, on an
    exception, or on leaving without ``commit()``) drains the repositories'
    event buffers, so a repository shared across units of work never hands
    a failed scope's events to a later commit. It does not undo the saves
    themselves: ``FakeRepository`` keeps what was saved.

    Parameters
    ----------
    repository:
        A single repository stored under the ``"default"`` key.
    repositories:
        A dict of named repositories merged into ``_repos``.
        Useful when a handler needs access to multiple repositories.
    fail_at:
        ``"flush"``, ``"write_outbox"`` or ``"commit"``: that hook of
        ``commit()`` raises *failure* once, in the next scope that reaches
        it, and the hooks after it do not run. The attribute is then reset
        to ``None``; set it again to inject another failure, as with
        ``FakeOutboxStore.fail_next_append``. ``None`` (the default) injects
        nothing.
    failure:
        The exception the hook raises. ``None`` (the default) raises a new
        ``RuntimeError`` each time.
    """

    def __init__(
        self,
        repository: Any | None = None,
        repositories: dict[str, Any] | None = None,
        *,
        fail_at: _FailAt | None = None,
        failure: BaseException | None = None,
    ) -> None:
        super().__init__()
        if repository is not None:
            self._repos["default"] = repository
        if repositories is not None:
            self._repos.update(repositories)
        self._rolled_back = False
        self.fail_at: _FailAt | None = fail_at
        self.failure: BaseException | None = failure

    @property
    def rolled_back(self) -> bool:
        """Whether the current scope rolled back (reset on each ``async with``)."""
        return self._rolled_back

    async def __aenter__(self) -> AbstractUnitOfWork:
        """Start a scope; ``rolled_back`` reports only this scope's rollback."""
        self._rolled_back = False
        return await super().__aenter__()

    def _inject(self, hook: _FailAt) -> None:
        """Raise the injected failure if *hook* is ``fail_at``, then disarm."""
        if self.fail_at != hook:
            return
        self.fail_at = None
        if self.failure is not None:
            raise self.failure
        raise RuntimeError(f"FakeUnitOfWork: injected {hook} failure")

    async def _flush(self) -> None:
        """No-op for in-memory testing, unless ``fail_at == "flush"``."""
        self._inject("flush")

    async def _write_outbox(self) -> None:
        """Write to the outbox as the base class does, unless ``fail_at`` says so."""
        self._inject("write_outbox")
        await super()._write_outbox()

    async def _commit(self) -> None:
        """No-op for in-memory testing, unless ``fail_at == "commit"``."""
        self._inject("commit")

    async def rollback(self) -> None:
        """Record the rollback, then drain the repositories' event buffers.

        This overrides ``rollback()`` rather than the ``_rollback()`` storage
        hook that ADR-068 tells adapters to override, on purpose: tests
        subclass the fake and override ``_rollback()`` to simulate a storage
        rollback (often a failing one) without calling ``super()``. Keeping
        the record here means ``rolled_back`` reports every rollback,
        whatever such a subclass does in ``_rollback()``.
        """
        self._rolled_back = True
        await super().rollback()
