from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import AliasChoices, AliasPath, BaseModel

from pydomain._describe import describe
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_store import EventStore
from pydomain.es.exceptions import SnapshotRestoreError, StreamNotFoundError
from pydomain.es.snapshot import (
    Snapshot,
    SnapshotPolicy,
    SnapshotSchemaPolicy,
    SnapshotStore,
)


def _top_level_aliases(model_cls: type[BaseModel]) -> dict[str, str]:
    """Map each top-level alias of *model_cls* that is not a field name to its
    field name.

    Covers ``alias``, ``serialization_alias`` and the plain-string forms of
    ``validation_alias`` (a string, the string choices of ``AliasChoices``
    and one-element ``AliasPath``s), so state written under any of them is
    read back by field name (B-74).  The first field to claim an alias keeps
    it.
    """
    fields = model_cls.model_fields
    renames: dict[str, str] = {}
    for name, info in fields.items():
        candidates: list[Any] = [info.alias, info.serialization_alias]
        validation = info.validation_alias
        choices = (
            validation.choices if isinstance(validation, AliasChoices) else [validation]
        )
        for choice in choices:
            if isinstance(choice, AliasPath):
                candidates.extend(choice.path if len(choice.path) == 1 else [])
            else:
                candidates.append(choice)
        for alias in candidates:
            if isinstance(alias, str) and alias not in fields:
                renames.setdefault(alias, name)
    return renames


class EventSourcedRepository[T: EventSourcedAggregateRoot[Any], TId]:
    """Concrete base class for repositories that persist event-sourced aggregates.

    ``save(aggregate)`` drains pending events, appends them to the event store
    with optimistic concurrency control, and optionally takes a snapshot if
    the policy (configured via the constructor) dictates.

    ``get_by_id(id_)`` reads the event stream to rebuild aggregate state,
    optionally using a snapshot (configured via the constructor) as a starting
    point for faster hydration.

    ``pull_events()`` drains the internal event buffer so the Unit of Work
    can stamp events with correlation/causation IDs before publishing.
    """

    def __init__(
        self,
        event_store: EventStore,
        aggregate_cls: type[T],
        snapshot_store: SnapshotStore | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
        snapshot_schema_policy: SnapshotSchemaPolicy | None = None,
    ) -> None:
        self._event_store = event_store
        self._aggregate_cls = aggregate_cls
        self._snapshot_store = snapshot_store
        self._snapshot_policy = snapshot_policy
        self._snapshot_schema_policy = snapshot_schema_policy
        self._collected_events: list[DomainEvent] = []

    @property
    def aggregate_type(self) -> str:
        """The type discriminator for this repository's aggregate.

        Used as the ``aggregate_type`` parameter when interacting with a
        :class:`SnapshotStore`.  Derived from the aggregate class name
        (``self._aggregate_cls.__name__``).
        """
        return self._aggregate_cls.__name__

    async def save(
        self,
        aggregate: T,
        command_id: UUID | None = None,
    ) -> None:
        """Persist pending events and optionally take a snapshot.

        1. Pull pending events via ``pull_events()``
        2. If no events, return
        3. Calculate ``expected_version = aggregate.version - len(events)``
        4. Call ``event_store.append_to_stream(
           str(aggregate.id), events, expected_version, command_id)``
        5. If both *snapshot_store* and *snapshot_policy* were provided via
           the constructor, evaluate ``snapshot_policy.should_snapshot(...)``.
           If True, call ``aggregate._take_snapshot()`` and
           ``snapshot_store.save(self.aggregate_type, snapshot)``.

        Parameters
        ----------
        aggregate:
            The event-sourced aggregate whose pending events to persist.
        command_id:
            Optional UUID uniquely identifying the command that produced
            these events.  When provided, the event store SHOULD reject
            duplicate submissions by raising
            :class:`~pydomain.es.exceptions.DuplicateCommandError`.

        Raises
        ------
        ConcurrencyError
            If the current stream length does not match the expected
            version (optimistic concurrency conflict).  This includes
            the case where the stream already exists but
            ``expected_version`` is ``0``.
        DuplicateCommandError
            If ``command_id`` was already processed for this aggregate.
        """
        events = aggregate.pull_events()
        if not events:
            return
        expected_version = aggregate.version - len(events)
        await self._event_store.append_to_stream(
            str(aggregate.id), events, expected_version, command_id=command_id
        )
        self._collected_events.extend(events)

        # Snapshot after successful append
        if self._snapshot_store is not None and self._snapshot_policy is not None:
            if self._snapshot_policy.should_snapshot(
                aggregate_type=self.aggregate_type,
                aggregate_id=str(aggregate.id),
                current_version=aggregate.version,
                pending_event_count=len(events),
            ):
                snapshot = aggregate._take_snapshot()  # pyright: ignore[reportPrivateUsage]
                await self._snapshot_store.save(self.aggregate_type, snapshot)

    async def get_by_id(
        self,
        id_: TId,
    ) -> T | None:
        """Load an aggregate, optionally using a snapshot for fast hydration.

        1. If a *snapshot_store* was provided via the constructor:
           a. ``snapshot = await snapshot_store.get(self.aggregate_type, str(id_))``
           b. If found and a *snapshot_schema_policy* is configured, validate
              the snapshot's ``schema_version`` against the aggregate's
              ``_snapshot_schema_version``.  If the policy rejects the snapshot,
              fall back to full replay (step 3).
           c. If the snapshot is valid: validate its state into the
              aggregate class with ``model_validate`` (computed keys
              dropped, keys of removed fields ignored; ADR-075), set
              ``version``, then call
              ``event_store.read_stream(str(id_), from_version=snapshot.version)``
              and replay remaining events.
        2. If no snapshot was found (or no *snapshot_store* configured), fall
           back to full replay: ``event_store.read_stream(str(id_))`` from
           version 0.
        3. If ``StreamNotFoundError`` is raised and no snapshot was used,
           return ``None``.

        Returns
        -------
        T | None
            The reconstituted aggregate, or ``None`` if no event stream
            exists for the given ID.

        Raises
        ------
        SnapshotRestoreError
            If a usable snapshot's state no longer validates into the
            aggregate class, or the aggregate's validators or
            ``model_post_init`` raise while it is restored.  Its message
            carries no stored value, only failing locations (which can
            include dict keys) and error codes, or an exception's type.
        """
        aggregate_id = str(id_)
        store = self._snapshot_store

        # Snapshot-first hydration: rebuild from snapshot then replay tail
        if store is not None:
            snapshot = await store.get(self.aggregate_type, aggregate_id)
            if snapshot is not None and self._snapshot_is_usable(snapshot):
                aggregate = self._restore(id_, snapshot)
                try:
                    stream = await self._event_store.read_stream(
                        aggregate_id, snapshot.version
                    )
                except StreamNotFoundError:
                    return aggregate
                for event in stream.events:
                    aggregate._replay(event)  # pyright: ignore[reportPrivateUsage]
                return aggregate

        # Fallback to full replay from scratch
        try:
            stream = await self._event_store.read_stream(aggregate_id)
        except StreamNotFoundError:
            return None
        aggregate = self._aggregate_cls(id=id_)
        for event in stream.events:
            aggregate._replay(event)  # pyright: ignore[reportPrivateUsage]
        return aggregate

    def _restore(self, id_: TId, snapshot: Snapshot) -> T:
        """Build the aggregate a snapshot describes, at the snapshot's version.

        The state is validated into the aggregate class with ``model_validate``
        (ADR-075), so nested models are rebuilt, containers are copied, and the
        aggregate's validators and ``model_post_init`` run on the restored state
        (B-53, B-54).  Keys of computed fields (their names and aliases) are
        dropped first, so a snapshot taken before they were left out still loads
        (B-56).  The state is validated by field name only; a top-level key that
        is a field's alias (validation or serialization) rather than a field
        name is renamed to that field first (B-74).  State that gives one field
        under two keys, its name and an alias or two aliases, is ambiguous and
        raises (B-78). The ``id`` passed to ``get_by_id()`` replaces any ``id``
        in the state.  Keys the class does not know are ignored, or rejected
        under ``extra="forbid"``.

        Raises
        ------
        SnapshotRestoreError
            If two keys of the state give the same field (B-78), or if
            validating the state raises any ``Exception``: a
            ``ValidationError``, or an error from the aggregate's
            validators or ``model_post_init`` (B-67).  There is no
            fallback to a full replay (B-55).
        """
        # Computed fields' names and aliases: an old snapshot may hold either.
        computed = {
            key
            for name, info in self._aggregate_cls.model_computed_fields.items()
            for key in (name, info.alias)
        }
        renames = _top_level_aliases(self._aggregate_cls)
        state: dict[str, Any] = {}
        source: dict[str, str] = {}
        for key, value in snapshot.state.items():
            if key in computed:
                continue
            # An alias key from an older snapshot is read as its field name.
            field = renames.get(key, key)
            if field in source:
                # Two keys give one field: the state is ambiguous (B-78).
                first, second = sorted((source[field], key), key=lambda k: k != field)
                raise SnapshotRestoreError(
                    self.aggregate_type,
                    str(id_),
                    snapshot.version,
                    f"ambiguous state for field {field!r} (keys {first!r}, {second!r})",
                )
            source[field] = key
            state[field] = value
        state["id"] = id_
        # Validated with the rest, so validators see it and nothing is
        # assigned (and re-validated) afterwards.
        state["version"] = snapshot.version
        try:
            # Snapshots are keyed by field name; aliases are renamed above, so
            # an alias that equals another field's name cannot capture its
            # value (B-74).
            return self._aggregate_cls.model_validate(
                state, by_alias=False, by_name=True
            )
        except Exception as exc:
            # Any failure of the user's model code too (B-67); a
            # BaseException that is not an Exception propagates.
            description = describe(exc)
        # Raised outside the except block: no cause and no context, so no
        # stored value reaches a traceback (ADR-071, B-55).
        raise SnapshotRestoreError(
            self.aggregate_type, str(id_), snapshot.version, description
        )

    def _snapshot_is_usable(self, snapshot: Snapshot) -> bool:
        """Check whether a loaded snapshot is compatible with the aggregate.

        If a ``snapshot_schema_policy`` is configured, delegates to its
        ``should_use_snapshot()`` method.  Otherwise, all snapshots are
        accepted (backward-compatible default).
        """
        if self._snapshot_schema_policy is None:
            return True
        expected = self._aggregate_cls._snapshot_schema_version  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        return self._snapshot_schema_policy.should_use_snapshot(
            snapshot, expected_schema_version=expected
        )

    def pull_events(self) -> list[DomainEvent]:
        """Drain and return collected domain events.

        Returns all events collected by ``save()`` since the last
        call to ``pull_events()``, then clears the internal buffer.

        Returns
        -------
        list[DomainEvent]
            Events collected since the last call.  Empty list if no
            events are pending.
        """
        events = self._collected_events
        self._collected_events = []
        return events
