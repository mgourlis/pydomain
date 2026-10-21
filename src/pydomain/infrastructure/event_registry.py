"""Event type registry for serialization and deserialization.

The ``EventRegistry`` maps between event type names and their Pydantic model
classes, enabling dynamic serialization/deserialization without compile-time
discriminated unions. When a type is not registered, deserialization falls
back to ``GenericDomainEvent`` (weak-schema mode).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, TypeGuard

from pydantic import BaseModel

from pydomain.es.exceptions import UpcastError

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
_DIGITS = re.compile("[0-9]+")


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

        With an ``UpcasterRegistry`` attached, the upcaster chain for the
        envelope's ``"version"`` (default 1) runs first. It must end at the
        class's version: the default of its ``event_version`` field, or 1
        for a model without one. That version is written to the payload's
        ``event_version`` before validation, so serializing the result and
        reading it again runs no upcaster (ADR-069). An envelope at a version
        newer than the class (written by newer code) is validated unchanged:
        no upcaster runs and its ``event_version`` is kept.

        A ``KeyError`` or ``ValidationError`` raised while upcasting or
        validating a registered type propagates; only an unregistered type
        selects the fallback.

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
        UpcastError
            If an upcaster fails, the chain from an older version stops
            short of the class's version or goes past it, or the class
            declares ``event_version`` without a default, or the stored
            version is not an int (only with an ``UpcasterRegistry``).
        """
        type_name = data.get("type")
        if type_name is None:
            msg = "Event payload missing required 'type' discriminator"
            raise ValueError(msg)
        payload = dict(data.get("data", {}))
        version = data.get("version", 1)
        try:
            cls = self.resolve(type_name)
        except KeyError:
            logger.warning(
                "Unregistered event type '%s'; using GenericDomainEvent",
                type_name,
            )
            return GenericDomainEvent(type=type_name, data=payload, version=version)
        if self._upcaster_registry is not None:
            payload = _upcast(self._upcaster_registry, cls, type_name, version, payload)
        return cls.model_validate(payload)


def _upcast(
    upcaster_registry: UpcasterRegistry,
    cls: type[BaseModel],
    type_name: str,
    stored: object,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Upcast *payload* to the version of *cls* and stamp ``event_version``.

    The class's version is the default of its ``event_version`` field (a
    ``default_factory`` is called), or 1 for a model without one. The stored
    version must be an int or a string of ASCII digits (``"2"`` is read as
    2). A payload stored at a newer version keeps its fields (written by
    newer code, e.g. during a rolling deploy); only a missing
    ``event_version`` is filled in from the envelope. Raises ``UpcastError``
    when the stored version is malformed, when the class's ``event_version``
    has no integer default, when an upcaster returns something other than a
    dict, or when the chain from an older version stops short of the class's
    version or goes past it (ADR-069).
    """
    class_version = _class_version(cls, type_name)
    version = _stored_version(stored, type_name)
    has_field = "event_version" in cls.model_fields
    if version > class_version:
        if has_field:
            payload.setdefault("event_version", version)
        return payload
    for upcaster_cls in upcaster_registry.resolve(type_name, version):
        payload = upcaster_cls().upcast(payload)
        if not isinstance(payload, dict):
            raise UpcastError(
                f"{upcaster_cls.__name__} returned {type(payload).__name__}, not a "
                f"dict, when upcasting {type_name!r} from v{version} to "
                f"v{upcaster_cls.target_version}"
            )
        version = upcaster_cls.target_version
    if version != class_version:
        raise UpcastError(
            f"Upcaster chain for {type_name!r} ends at v{version}, "
            f"but the class is at v{class_version}"
        )
    if has_field:
        payload["event_version"] = version
    return payload


def _class_version(cls: type[BaseModel], type_name: str) -> int:
    """The default of *cls*'s ``event_version`` field, or 1 without the field."""
    field = cls.model_fields.get("event_version")
    if field is None:
        return 1
    no_default = UpcastError(
        f"{type_name!r} must declare a default event_version to be read "
        f"through an UpcasterRegistry"
    )
    try:
        default = field.get_default(call_default_factory=True)
    except ValueError as exc:  # a default_factory that needs the validated data
        raise no_default from exc
    if not _is_version(default):
        raise no_default
    return default


def _stored_version(stored: object, type_name: str) -> int:
    """The envelope's version as an int; a string of ASCII digits is converted."""
    if isinstance(stored, str) and _DIGITS.fullmatch(stored):
        return int(stored)
    if not _is_version(stored):
        raise UpcastError(f"Stored version of {type_name!r} is not an int")
    return stored


def _is_version(value: object) -> TypeGuard[int]:
    """True for an ``int`` that is not a ``bool``."""
    return isinstance(value, int) and not isinstance(value, bool)
