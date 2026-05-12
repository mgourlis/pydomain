"""Idempotency primitives for the CQRS pipeline.

Defines the ``ProcessedCommandStore`` Protocol (abstraction for
tracking processed command IDs) and the ``MISSING`` sentinel that
distinguishes "never processed" from a cached ``None`` result.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

MISSING: Any = object()
"""Sentinel returned by :class:`ProcessedCommandStore` when no cached result exists."""


@runtime_checkable
class ProcessedCommandStore(Protocol):
    """Protocol for storage backends that track which command IDs have been processed.

    Implementations store ``CommandResult`` values keyed by command UUID
    so that duplicate commands can return the cached result instead of
    executing the handler again.
    """

    async def get(self, command_id: UUID) -> Any:
        """Return the cached result for *command_id*, or ``MISSING``."""
        ...

    async def set(self, command_id: UUID, result: Any) -> None:
        """Persist *result* for *command_id*."""
        ...

    async def contains(self, command_id: UUID) -> bool:
        """Return ``True`` if *command_id* has already been processed."""
        ...
