"""Event upcasting infrastructure for schema versioning.

Provides the base class for event upcasters and a registry that
discovers and chains upcasters to migrate events across schema versions.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydomain.es.exceptions import UpcastError


class EventUpcaster:
    """Base class for event upcasters.

    Subclasses must declare their source and target event schema versions
    via class variables and implement :meth:`_transform` to perform the
    actual payload migration. ``_transform`` need not set
    ``event_version``: ``EventRegistry.deserialize`` writes the version the
    chain reached before validation (ADR-069).

    Class variables
    ---------------
    source_type : str
        The event type name to upcast FROM.
    source_version : int
        The event schema version to upcast FROM.
    target_version : int
        The event schema version to upcast TO.
    """

    source_type: ClassVar[str]
    source_version: ClassVar[int]
    target_version: ClassVar[int]

    def upcast(self, event: dict[str, Any]) -> dict[str, Any]:
        """Transform *event* from ``source_version`` to ``target_version``.

        Parameters
        ----------
        event : dict
            The raw event payload dict at ``source_version``.

        Returns
        -------
        dict
            The transformed event payload dict at ``target_version``.

        Raises
        ------
        UpcastError
            If the transformation fails (e.g. missing expected keys or
            invalid data in the source payload).
        """
        try:
            return self._transform(event)
        except Exception as exc:
            raise UpcastError(
                f"Failed to upcast {self.source_type!r} from v{self.source_version} "
                f"to v{self.target_version}: {exc}"
            ) from exc

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        """Implement the actual payload transformation in subclasses.

        Parameters
        ----------
        event : dict
            The raw event payload dict at ``source_version``.

        Returns
        -------
        dict
            The transformed event payload dict at ``target_version``.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement _transform")


class UpcasterRegistry:
    """Registry that stores and resolves upcasters by event type and version.

    Upcasters are keyed by ``(source_type, source_version)``, one per key,
    and can be chained together to migrate an event across multiple schema
    versions. Every registered upcaster moves its event type to a higher
    version, so a chain resolved from registered upcasters cannot loop
    (ADR-069); the cycle guard in :meth:`resolve` catches class variables
    changed after registration.
    """

    def __init__(self) -> None:
        self._upcasters: dict[tuple[str, int], type[EventUpcaster]] = {}

    def register(self, upcaster: type[EventUpcaster]) -> None:
        """Register an upcaster.

        Parameters
        ----------
        upcaster : type[EventUpcaster]
            The upcaster class to register. Its ``source_type`` and
            ``source_version`` class variables are used as the lookup key.

        Raises
        ------
        TypeError
            If *upcaster* does not define ``source_type``,
            ``source_version`` and ``target_version``.
        ValueError
            If ``target_version`` is not greater than ``source_version``, or
            an upcaster is already registered for the same
            ``(source_type, source_version)``.
        """
        if not all(
            hasattr(upcaster, name)
            for name in ("source_type", "source_version", "target_version")
        ):
            raise TypeError(
                f"{upcaster.__name__} must define source_type, source_version "
                f"and target_version class variables"
            )
        key = (upcaster.source_type, upcaster.source_version)
        if upcaster.target_version <= upcaster.source_version:
            raise ValueError(
                f"{upcaster.__name__} upcasts {upcaster.source_type!r} from "
                f"v{upcaster.source_version} to v{upcaster.target_version}; "
                f"target_version must be greater than source_version"
            )
        registered = self._upcasters.get(key)
        if registered is not None:
            raise ValueError(
                f"An upcaster for {upcaster.source_type!r} "
                f"v{upcaster.source_version} is already registered: "
                f"{registered.__name__}"
            )
        self._upcasters[key] = upcaster

    def resolve(
        self, source_type: str, source_version: int
    ) -> list[type[EventUpcaster]]:
        """Resolve an ordered chain of upcasters for the given event.

        Follows registered upcasters from *source_version* through
        successive ``target_version`` hops until no further upcaster is
        registered. The chain does not know the event class, so it may stop
        short of the class's version; ``EventRegistry.deserialize`` checks
        that it ends there (ADR-069).

        Parameters
        ----------
        source_type : str
            The event type name to resolve upcasters for.
        source_version : int
            The current schema version of the event.

        Returns
        -------
        list[type[EventUpcaster]]
            An ordered list of upcaster classes to apply in sequence, or an
            empty list if no upcasters are registered for this
            ``(source_type, source_version)``.

        Raises
        ------
        UpcastError
            If the hops revisit a version (a cycle).
        """
        chain: list[type[EventUpcaster]] = []
        version = source_version
        visited: set[int] = set()
        while True:
            if version in visited:
                raise UpcastError(
                    f"Upcaster cycle detected for {source_type!r} at version {version}"
                )
            visited.add(version)
            key = (source_type, version)
            upcaster = self._upcasters.get(key)
            if upcaster is None:
                break
            chain.append(upcaster)
            version = upcaster.target_version
        return chain
