"""Event type registry for serialization and deserialization.

The ``EventRegistry`` maps between event type names and their Pydantic model
classes, enabling dynamic serialization/deserialization without compile-time
discriminated unions. When a type is not registered, deserialization falls
back to ``GenericDomainEvent`` (weak-schema mode).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from pydomain.es.upcasting import UpcasterRegistry

logger = logging.getLogger("pydomain.event_registry")


class GenericDomainEvent(BaseModel):
    """Weak-schema fallback for unrecognized event types.

    When deserializing an event whose type is not registered in the
    ``EventRegistry``, this model carries the raw dict data instead of
    raising an error. Useful for legacy events that have been removed
    from the codebase.

    Parameters
    ----------
    type:
        The original type discriminator from the serialized data.
    data:
        The raw event payload dict.
    """

    type: str
    data: dict[str, Any]
    version: int = 1


_REGISTRY_ERROR_TMPL = "Event type '%s' is not registered in the EventRegistry"


class EventRegistry:
    """Maps event type names to their Pydantic model classes.

    Unlike Pydantic ``Discriminator`` unions, the registry is dynamic --
    users register their own event types at startup rather than enumerating
    them in a type annotation.

    Parameters
    ----------
    _registry:
        Internal mapping of type name strings to Pydantic model classes.
    """

    def __init__(self, upcaster_registry: UpcasterRegistry | None = None) -> None:
        self._registry: dict[str, type[BaseModel]] = {}
        self._upcaster_registry = upcaster_registry

    def register(self, event_class: type[BaseModel]) -> None:
        """Register a Pydantic model class by its ``__name__``.

        Raises ``ValueError`` if the class is already registered.

        Parameters
        ----------
        event_class:
            The Pydantic model class to register.
        """
        name = event_class.__name__
        if name in self._registry:
            msg = f"Event type '{name}' is already registered"
            raise ValueError(msg)
        self._registry[name] = event_class

    def resolve(self, type_name: str) -> type[BaseModel]:
        """Return the registered class for a type name.

        Parameters
        ----------
        type_name:
            The name to look up.

        Returns
        -------
        type[BaseModel]
            The registered Pydantic model class.

        Raises
        ------
        KeyError
            If the type name is not registered.
        """
        if type_name not in self._registry:
            raise KeyError(_REGISTRY_ERROR_TMPL % type_name)
        return self._registry[type_name]

    def type_name(self, event: BaseModel) -> str:
        """Return the type name for an event instance.

        Parameters
        ----------
        event:
            The event instance.

        Returns
        -------
        str
            The event class ``__name__``.
        """
        return type(event).__name__

    def serialize(self, event: BaseModel) -> dict[str, Any]:
        """Serialize an event to a dict with type discriminator.

        Returns ``{"type": "<type_name>", "data": {event fields...}}``.

        Parameters
        ----------
        event:
            The event instance to serialize.

        Returns
        -------
        dict[str, Any]
            The serialized dict with type discriminator.
        """
        result: dict[str, Any] = {
            "type": self.type_name(event),
            "data": event.model_dump(),
        }
        version = getattr(event, "event_version", None)
        if version is not None:
            result["version"] = version
        return result

    def deserialize(self, data: dict[str, Any]) -> BaseModel | GenericDomainEvent:
        """Deserialize an event dict into a model instance.

        If the type is not registered, returns ``GenericDomainEvent``
        carrying the raw data (weak-schema fallback).

        Parameters
        ----------
        data:
            Dict with ``"type"`` and ``"data"`` keys.

        Returns
        -------
        BaseModel | GenericDomainEvent
            The deserialized event instance or weak-schema fallback.

        Raises
        ------
        ValueError
            If the ``"type"`` discriminator is missing or ``None``.
        """
        type_name = data.get("type")
        if type_name is None:
            msg = "Event payload missing required 'type' discriminator"
            raise ValueError(msg)
        payload = data.get("data", {})
        version = data.get("version", 1)
        try:
            cls = self.resolve(type_name)
            if self._upcaster_registry is not None:
                upcasters = self._upcaster_registry.resolve(type_name, version)
                if upcasters:
                    for upcaster_cls in upcasters:
                        upcaster = upcaster_cls()
                        payload = upcaster.upcast(payload)
            return cls.model_validate(payload)
        except KeyError:
            logger.warning(
                "Unregistered event type '%s'; using GenericDomainEvent",
                type_name,
            )
            return GenericDomainEvent(type=type_name, data=payload, version=version)
