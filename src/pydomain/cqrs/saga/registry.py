"""SagaRegistry — maps event types to saga classes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .saga import Saga

logger = logging.getLogger("pydomain.saga")


class SagaRegistry:
    """Injectable registry that maps ``event_type`` → ``list[type[Saga]]``.

    Multiple sagas can react to the same event type.  Saga classes are
    also registered by name for lookup during recovery / timeout handling.

    Quick registration via ``register_saga``::

        saga_registry.register_saga(OrderFulfillmentSaga)

    Manual registration via ``register``::

        saga_registry.register(OrderCreated, OrderFulfillmentSaga)
    """

    def __init__(self) -> None:
        self._event_map: dict[type, list[type[Saga[Any]]]] = {}
        self._type_map: dict[str, type[Saga[Any]]] = {}

    # ── Bulk Registration ────────────────────────────────────────────

    def register_saga(
        self,
        saga_class: type[Saga[Any]],
        *,
        strict: bool = False,
    ) -> None:
        """Register a saga class for all events declared in ``listened_events()``.

        This is the preferred registration method.  It reads the class-level
        ``listens_to`` declaration and registers the saga for each event type
        automatically.

        If ``listens_to`` returns an empty list, only the type-name mapping
        is registered (for recovery lookups).

        Parameters
        ----------
        saga_class:
            The saga class to register.
        strict:
            When ``True``, raise :class:`SagaConfigurationError` if
            ``listens_to`` is empty instead of logging a warning.
        """
        from pydomain.cqrs.saga.exceptions import SagaConfigurationError

        events = saga_class.listened_events()
        if events:
            for event_type in events:
                self.register(event_type, saga_class)
        else:
            # Still register by name even if no events declared.
            self.register_type(saga_class)
            msg = (
                f"Saga {saga_class.__name__} has no listened events "
                f"(listens_to is empty). Set listens_to on the saga "
                f"class to enable event registration."
            )
            if strict:
                raise SagaConfigurationError(msg)
            logger.warning(msg)

    # ── Per-Event Registration ───────────────────────────────────────

    def register(self, event_type: type, saga_class: type[Saga[Any]]) -> None:
        """Register *saga_class* as a handler for *event_type*."""
        if event_type not in self._event_map:
            self._event_map[event_type] = []

        if saga_class not in self._event_map[event_type]:
            self._event_map[event_type].append(saga_class)
            logger.debug(
                "Registered Saga %s for event %s",
                saga_class.__name__,
                event_type.__name__,
            )

        # Also register by name for recovery lookups.
        self.register_type(saga_class)

    def register_type(self, saga_class: type[Saga[Any]]) -> None:
        """Register a saga class by its ``__name__`` only (no event binding)."""
        self._type_map[saga_class.__name__] = saga_class

    # ── Queries ──────────────────────────────────────────────────────

    @property
    def registered_event_types(self) -> set[type]:
        """Return all event types that have at least one saga registered."""
        return set(self._event_map.keys())

    def get_sagas_for_event(self, event_type: type) -> list[type[Saga[Any]]]:
        """Return all saga classes registered for *event_type*."""
        return self._event_map.get(event_type, [])

    def get_saga_type(self, name: str) -> type[Saga[Any]] | None:
        """Look up a saga class by its ``__name__``."""
        return self._type_map.get(name)

    # ── Housekeeping ─────────────────────────────────────────────────

    def clear(self) -> None:
        """Remove all registrations."""
        self._event_map.clear()
        self._type_map.clear()
