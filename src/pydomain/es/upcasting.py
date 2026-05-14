"""Event upcasting infrastructure for schema versioning.

Provides the base class for event upcasters and a registry that
discovers and chains upcasters to migrate events across schema versions.
"""

from __future__ import annotations

from typing import ClassVar

from pydomain.es.exceptions import UpcastError


class EventUpcaster:
    """Base class for event upcasters.

    Subclasses must declare their source and target event schema versions
    via class variables and implement :meth:`_transform` to perform the
    actual payload migration.

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

    def upcast(self, event: dict) -> dict:
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

    def _transform(self, event: dict) -> dict:
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

    Upcasters are keyed by ``(source_type, source_version)`` and can be
    chained together to migrate an event across multiple schema versions.
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
            If *upcaster* is missing required class variables.
        """
        if not hasattr(upcaster, "source_type") or not hasattr(
            upcaster, "source_version"
        ):
            raise TypeError(
                f"{upcaster.__name__} must define source_type and source_version "
                f"class variables"
            )
        key = (upcaster.source_type, upcaster.source_version)
        self._upcasters[key] = upcaster

    def resolve(
        self, source_type: str, source_version: int
    ) -> list[type[EventUpcaster]]:
        """Resolve an ordered chain of upcasters for the given event.

        Follows registered upcasters from *source_version* through
        successive ``target_version`` hops until no further upcaster is
        registered.

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
        """
        chain: list[type[EventUpcaster]] = []
        version = source_version
        while True:
            key = (source_type, version)
            upcaster = self._upcasters.get(key)
            if upcaster is None:
                break
            chain.append(upcaster)
            version = upcaster.target_version
        return chain
