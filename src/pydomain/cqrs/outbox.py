"""Outbox primitives for reliable outbound integration event delivery.

The ``OutboxStore`` protocol defines the persistence contract for the
transactional outbox: integration events are written inside the same
transaction as the aggregate state change, so a committed state change can
never lose its outbound event to a broker outage.

``OutboundEventRegistry`` performs the Anti-Corruption Layer translation
from ``DomainEvent`` to ``IntegrationEvent`` and records the topic the
resulting integration event travels on.  Translation happens at **write**
time — inside ``AbstractUnitOfWork._write_outbox()`` — so the outbox row
*is* the wire format and is never re-interpreted by later code.

See ADR-063.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent

logger = logging.getLogger("pydomain.outbox")

__all__ = [
    "OutboundEventRegistry",
    "OutboxEntry",
    "OutboxStore",
    "OutboxWriter",
]

_TRANSLATOR_ERROR_TMPL = "No outbound translation registered for topic '%s'"


class OutboxEntry(BaseModel):
    """A durable record of an integration event awaiting publication.

    Written inside the business transaction and never mutated afterwards.
    The payload is the serialized integration event, so the row carries the
    broker wire format directly — no conversion layer is required because
    ``IntegrationEvent`` is primitives-only by validation.

    Parameters
    ----------
    message_id:
        The integration event's own ``event_id``. Used for
        at-least-once deduplication by consumers.
    topic:
        The topic or routing key the event travels on.
    payload:
        The serialized integration event (``model_dump()``).
    event_version:
        Schema version of the payload, for forward-compatible reads.
    attempts:
        How many delivery attempts have already failed. Maintained by the
        store: ``fetch_unpublished`` returns the current count so the relay
        can compute an increasing backoff.
    occurred_at:
        When the originating domain event occurred.
    correlation_id:
        Business process identifier, propagated from the domain event.
    causation_id:
        The ID of the command or event that directly caused this one.
    """

    model_config = ConfigDict(frozen=True)

    message_id: str
    topic: str
    payload: dict[str, Any]
    event_version: int = 1
    attempts: int = 0
    occurred_at: datetime
    correlation_id: str | None = None
    causation_id: str | None = None


@runtime_checkable
class OutboxStore(Protocol):
    """Protocol for persisting the transactional outbox.

    Implementations are responsible for **atomicity and concurrency
    safety**.  Two consequences follow, and both are the adapter's
    responsibility rather than this contract's:

    - ``fetch_unpublished`` must not hand the same entry to two concurrent
      relays.  A database adapter typically achieves this with row-level
      locking plus a claim timestamp (for example ``SELECT ... FOR UPDATE
      SKIP LOCKED``).
    - Entries claimed by a relay that then crashed must eventually become
      fetchable again once the claim goes stale.

    Neither concern is expressed in this protocol: claim mechanics are a
    storage detail, and inflating the port would push a database locking
    strategy into every adapter.  This mirrors ``ProcessedMessageStore``,
    whose docstring places the same expectation on implementations.
    """

    async def append(self, entries: Sequence[OutboxEntry]) -> None:
        """Persist entries within the caller's transaction.

        Called from ``AbstractUnitOfWork._write_outbox()``, so the entries
        commit atomically with the aggregate state change.

        Parameters
        ----------
        entries:
            The entries to persist. Implementations must not publish them.

        Raises
        ------
        CQRSError
            If persisting the entries fails.
        """
        ...

    async def fetch_unpublished(self, limit: int = 100) -> list[OutboxEntry]:
        """Return entries that have not yet been published.

        Must be safe under concurrent relays — see the protocol docstring.

        Parameters
        ----------
        limit:
            Maximum number of entries to return.

        Returns
        -------
        list[OutboxEntry]
            Up to ``limit`` unpublished entries, oldest first. Entries must
            reflect their current ``attempts`` count, and must exclude any
            whose next attempt time has not yet passed.
        """
        ...

    async def mark_published(self, message_ids: Sequence[str]) -> None:
        """Mark entries as successfully published.

        Called only after the broker has accepted the event. An entry must
        never be marked published when publication failed, or the message
        is lost.

        Parameters
        ----------
        message_ids:
            The ``message_id`` values to mark as published.
        """
        ...

    async def mark_failed(
        self,
        message_id: str,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        """Record a delivery failure and schedule the next attempt.

        Parameters
        ----------
        message_id:
            The ``message_id`` that failed to publish.
        error:
            A description of the failure, for operator diagnostics.
        next_attempt_at:
            When the entry may next be returned by
            ``fetch_unpublished``. Implementations should hide the entry
            from ``fetch_unpublished`` until this time passes.

        Notes
        -----
        Implementations must increment the entry's ``attempts`` count so
        the relay can compute an increasing backoff and know when the
        entry's retry budget is exhausted.
        """
        ...

    async def mark_dead_lettered(self, message_id: str, *, error: str) -> None:
        """Move an entry to the dead-letter state, permanently.

        Called when an entry has exhausted its retry budget — see
        ``OutboundEventGateway.max_attempts``. The entry must never be
        returned by ``fetch_unpublished`` again, and must never be
        reported as published: it was not delivered.

        Dead-lettered entries remain visible to operators for inspection
        and manual replay. Whether they live in the outbox table behind a
        status column or in a separate store is an adapter decision.

        Parameters
        ----------
        message_id:
            The ``message_id`` that exhausted its retry budget.
        error:
            The last failure, for operator diagnostics.

        Notes
        -----
        This call records the entry's **final** failed attempt, so
        implementations must also count it: an entry dead-lettered with
        ``max_attempts=10`` must report ``attempts == 10``, not ``9``.
        Otherwise the counter under-reports by one at exactly the moment
        an operator is reading it.
        """
        ...


@dataclass(frozen=True)
class _Registration:
    """Internal type pairing an integration class with its topic and translator."""

    integration_class: type[IntegrationEvent]
    topic: str
    translator: Callable[[DomainEvent], IntegrationEvent | None]


class OutboundEventRegistry:
    """Maps domain event types to their outbound integration events.

    Registration is synchronous and performs no I/O — it updates an
    in-memory mapping only.  Re-registering a domain type **overwrites**
    the previous registration, enabling hot-swap of integration event
    versions without a restart, mirroring ``InboundEventGateway``.

    The registry serves both directions of the outbound path:

    - :meth:`to_entries` — used at write time to translate collected
      domain events into outbox entries.
    - :meth:`resolve` — used at delivery time to rehydrate a stored
      payload back into a typed integration event, resolving the class
      from the topic alone.
    """

    def __init__(self) -> None:
        self._by_domain_type: dict[type[DomainEvent], _Registration] = {}
        self._by_topic: dict[str, type[IntegrationEvent]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_translation[T: IntegrationEvent](
        self,
        domain_type: type[DomainEvent],
        integration_class: type[T],
        topic: str,
        translator: Callable[[DomainEvent], T | None],
    ) -> None:
        """Register an outbound translation for a domain event type.

        Parameters
        ----------
        domain_type:
            The domain event class to translate from.
        integration_class:
            The integration event class to translate to. Used to rehydrate
            stored payloads on the delivery path.
        topic:
            The topic or routing key the translated event travels on.
        translator:
            A **pure** callable that converts a domain event into an
            integration event, or ``None`` to keep the domain event
            internal. Translators run inside the database transaction, so
            they must not perform I/O.
        """
        self._by_domain_type[domain_type] = _Registration(
            integration_class=integration_class,
            topic=topic,
            translator=translator,
        )
        self._by_topic[topic] = integration_class

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def to_entries(self, events: Sequence[DomainEvent]) -> list[OutboxEntry]:
        """Translate domain events into outbox entries.

        Events with no registration, or whose translator returns ``None``,
        produce no entry — they remain internal to the bounded context.

        Parameters
        ----------
        events:
            The stamped domain events collected by the Unit of Work.

        Returns
        -------
        list[OutboxEntry]
            One entry per domain event that maps to an integration event.

        Raises
        ------
        Exception
            Whatever a translator raises propagates unchanged. Translators
            run inside the database transaction, so a failure here aborts
            the commit rather than silently dropping the event.
        """
        entries: list[OutboxEntry] = []
        for event in events:
            entry = self.to_entry(event)
            if entry is not None:
                entries.append(entry)
        return entries

    def to_entry(self, event: DomainEvent) -> OutboxEntry | None:
        """Translate a single domain event into an outbox entry.

        Parameters
        ----------
        event:
            The stamped domain event to translate.

        Returns
        -------
        OutboxEntry | None
            The entry, or ``None`` when the event stays internal.
        """
        registration = self._by_domain_type.get(type(event))
        if registration is None:
            return None

        integration_event = registration.translator(event)
        if integration_event is None:
            return None

        return OutboxEntry(
            message_id=integration_event.event_id,
            topic=registration.topic,
            payload=integration_event.model_dump(),
            event_version=getattr(integration_event, "event_version", 1),
            occurred_at=event.occurred_at,
            correlation_id=str(event.correlation_id) if event.correlation_id else None,
            causation_id=str(event.causation_id) if event.causation_id else None,
        )

    # ------------------------------------------------------------------
    # Delivery path
    # ------------------------------------------------------------------

    def resolve(self, topic: str) -> type[IntegrationEvent]:
        """Return the integration event class registered for a topic.

        Parameters
        ----------
        topic:
            The topic or routing key to look up.

        Returns
        -------
        type[IntegrationEvent]
            The registered integration event class.

        Raises
        ------
        KeyError
            If no registration exists for the topic. A stored entry whose
            topic is unknown is a poison row — callers must not treat it
            as delivered.
        """
        if topic not in self._by_topic:
            msg = _TRANSLATOR_ERROR_TMPL % topic
            raise KeyError(msg)
        return self._by_topic[topic]


class OutboxWriter:
    """Translates collected domain events and appends them to the outbox.

    This is the write-side join between :class:`OutboundEventRegistry` and
    an :class:`OutboxStore`.  Compose it into your Unit of Work rather than
    inheriting from a library base class — ADR-001 rejects forcing extra
    inheritance on user UoWs, so the library ships the mechanism and you
    wire it in one line::

        class AppUnitOfWork(AbstractUnitOfWork):
            def __init__(self, writer: OutboxWriter) -> None:
                super().__init__()
                self._writer = writer

            async def _write_outbox(self) -> None:
                await self._writer.write(self.collect_events())

    The contract it owns:

    - A domain event with no registered translation produces no entry —
      registration *is* the declaration of intent to publish.
    - A translator returning ``None`` produces no entry.
    - A translator that raises propagates, aborting the transaction. A
      translation bug must not commit state stripped of its event.
    - An empty translation result skips the store call entirely.

    Parameters
    ----------
    store:
        The outbox to append to.
    registry:
        Resolves domain event types to their outbound translation.
    """

    def __init__(
        self,
        store: OutboxStore,
        registry: OutboundEventRegistry,
    ) -> None:
        self._store = store
        self._registry = registry

    async def write(self, events: Sequence[DomainEvent]) -> int:
        """Translate *events* and append the resulting entries.

        Parameters
        ----------
        events:
            The stamped domain events collected by the Unit of Work —
            typically ``self.collect_events()``.

        Returns
        -------
        int
            The number of entries appended. ``0`` when no collected event
            maps to an integration event.

        Raises
        ------
        Exception
            Whatever a translator raises propagates unchanged, aborting
            the surrounding transaction.
        CQRSError
            If appending to the outbox fails.
        """
        entries = self._registry.to_entries(events)
        if not entries:
            return 0
        await self._store.append(entries)
        return len(entries)
