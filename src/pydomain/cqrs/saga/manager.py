"""SagaManager — orchestrates saga lifecycle (load → handle → save → dispatch)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydomain._describe import describe
from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.saga.hydration import hydrate_command
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.id_generator import Uuid7Generator

if TYPE_CHECKING:
    from pydomain.cqrs.commands import Command
    from pydomain.cqrs.handlers import EventHandler
    from pydomain.ddd.domain_event import DomainEvent

    from .registry import SagaRegistry
    from .repository import SagaRepository
    from .saga import Saga

logger = logging.getLogger("pydomain.saga")

__all__ = ["CommandDispatcher", "EventHandlerRegistrar", "SagaManager"]


class CommandDispatcher(Protocol):
    """What ``SagaManager`` sends its commands through (ADR-077).

    ``dispatch`` runs the command and **publishes the domain events it
    raised** to their handlers, so a saga sees the events its own commands
    cause. :class:`~pydomain.infrastructure.message_bus.MessageBus` does;
    a bare :class:`~pydomain.cqrs.command_bus.CommandBus` returns the events
    instead, and ``SagaManager`` refuses it. The return value is ignored.
    """

    async def dispatch(self, command: Command[Any], /) -> Any:
        """Run *command* and publish the events it raised."""
        ...


class EventHandlerRegistrar(Protocol):
    """What :meth:`SagaManager.bind_to` registers its event handler with."""

    def register_event(
        self, event_type: type[DomainEvent], handler: EventHandler[DomainEvent], /
    ) -> None:
        """Register *handler* for *event_type*."""
        ...


def _failure_of(exc: BaseException) -> BaseException:
    """The handler's own failure behind a ``CommandExecutionError``."""
    if isinstance(exc, CommandExecutionError) and exc.__cause__ is not None:
        return exc.__cause__
    return exc


def _retries_exhausted(state: SagaState) -> bool:
    """Whether the saga has used up its retries (ADR-077, B-49, B-85).

    ``max_retries`` counts retries after the first attempt, so the saga is
    out of retries once it has failed more than ``max_retries`` times.
    ``None`` allows any number.
    """
    return state.max_retries is not None and state.retry_count > state.max_retries


def _serialize_command_for_pending(command: Command[Any]) -> dict[str, Any]:
    """Serialize a command for storage in ``pending_commands``."""
    return {
        "command_type": type(command).__name__,
        "module_name": type(command).__module__,
        "data": command.model_dump(),
        "dispatched": False,
    }


class SagaManager:
    """Orchestrates saga lifecycle: load → handle → save → dispatch.

    For each incoming event the manager:

    1. Finds all saga classes registered for the event type.
    2. Loads or creates a ``SagaState`` from the repository.
    3. Instantiates the ``Saga`` and calls ``handle(event)``.
    4. Saves state back to the repository.
    5. Dispatches pending commands via the ``command_bus``, a
       :class:`CommandDispatcher` such as the ``MessageBus``.

    Raises:
        TypeError: when ``command_bus`` is a bare ``CommandBus``, which
            returns a command's events instead of publishing them, so the
            saga would never see them (ADR-077).
    """

    def __init__(
        self,
        repository: SagaRepository,
        registry: SagaRegistry,
        command_bus: CommandDispatcher,
    ) -> None:
        if isinstance(command_bus, CommandBus):
            raise TypeError(
                "SagaManager needs a dispatcher that publishes the events a "
                "command raises; a CommandBus returns them instead, so the "
                "saga would never see them. Pass the MessageBus (ADR-077)."
            )
        self.repository = repository
        self.registry = registry
        self.command_bus = command_bus
        self._id_generator = Uuid7Generator()

    # ── Event Dispatcher Integration ─────────────────────────────────

    def bind_to(self, event_dispatcher: EventHandlerRegistrar) -> None:
        """Auto-register this manager as an event handler with the dispatcher.

        Reads all event types from the :class:`SagaRegistry` and registers
        :meth:`handle` for each one.  The *event_dispatcher* must provide a
        ``register_event(event_type, handler)`` method (e.g.
        :class:`~pydomain.infrastructure.message_bus.MessageBus`).

        Parameters
        ----------
        event_dispatcher:
            An :class:`EventHandlerRegistrar`: any object with a
            ``register_event(event_type, handler)`` method.
        """
        for event_type in self.registry.registered_event_types:
            event_dispatcher.register_event(event_type, self.handle)

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _trace_command(
        cmd: Command[Any],
        state: SagaState,
        causation_id: UUID | None = None,
    ) -> Command[Any]:
        """Propagate tracing IDs onto a command via ``model_copy``."""
        return cmd.model_copy(
            update={
                "correlation_id": state.correlation_id,
                "causation_id": causation_id if causation_id is not None else state.id,
            }
        )

    # ── Core processing ──────────────────────────────────────────────

    async def _create_initial_state(
        self,
        saga_class: type[Saga[Any]],
        saga_type_name: str,
        correlation_id: UUID,
        causation_id: UUID | None = None,
    ) -> SagaState:
        """Create and persist initial saga state."""
        state_cls = getattr(saga_class, "state_class", SagaState)
        state = state_cls(
            id=self._id_generator.generate(),  # pyright: ignore[reportCallIssue]
            saga_type=saga_type_name,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        await self.repository.save(state)
        return state

    async def _dispatch_and_persist_commands(
        self, state: SagaState, commands: list[Command[Any]]
    ) -> None:
        """Dispatch commands and persist state after each for recovery.

        Uses the starting offset into ``pending_commands`` so that
        partial dispatches from previous rounds are not corrupted.
        """
        start = len(state.pending_commands) - len(commands)
        for i, cmd in enumerate(commands):
            await self.command_bus.dispatch(cmd)
            state.pending_commands[start + i]["dispatched"] = True
            await self.repository.save(state)

    async def _dispatch_compensations(
        self,
        state: SagaState,
        commands: list[Command[Any]],
        forward: Sequence[Command[Any]] = (),
        causation_id: UUID | None = None,
    ) -> None:
        """Dispatch compensation commands and transition to terminal state.

        First sends the *forward* commands queued before compensation began
        (a ``fail=True`` step's command, ADR-077), traced with
        *causation_id* (the step's event), then the compensations, traced
        with ``causation_id = state.id``; ``correlation_id`` stays constant.
        Each command is dispatched on its own. A failure of either kind is
        recorded in ``state.failed_compensations``, with the exception's
        type name as the error, never its message (ADR-071), and the
        remaining commands are still sent.

        After dispatching, transitions the saga to ``COMPENSATED``
        if no failures were recorded (hydration or dispatch),
        or ``FAILED`` otherwise.
        """
        traced = [self._trace_command(cmd, state, causation_id) for cmd in forward]
        traced += [self._trace_command(cmd, state) for cmd in commands]
        for cmd in traced:
            try:
                await self.command_bus.dispatch(cmd)
            except Exception as comp_err:
                state.record_failed_compensation(
                    command_type=type(cmd).__name__,
                    data=cmd.model_dump(),
                    module_name=type(cmd).__module__,
                    error=describe(comp_err),
                )
                logger.error(
                    "Compensation command %s failed for saga %s: %s",
                    type(cmd).__name__,
                    state.id,
                    comp_err,
                )

        # Transition to terminal state based on results.
        # execute_compensations() deliberately leaves this to the
        # manager so dispatch results can be taken into account.
        if state.failed_compensations:
            state.status = SagaStatus.FAILED
        else:
            state.status = SagaStatus.COMPENSATED
        state.touch()

    async def _handle_event_error(
        self,
        saga: Saga[Any],
        state: SagaState,
        handler_err: Exception,
    ) -> None:
        """Handle an exception from saga event processing.

        If the saga is already COMPENSATING, dispatch queued
        compensations.  Otherwise, attempt to fail with compensation.
        Forward commands queued before the handler raised are dropped.
        The failure reason stored in ``state.error`` is the exception's
        type name, never its message (ADR-071).
        """
        if state.status == SagaStatus.COMPENSATING:
            commands = saga.collect_commands()
            await self._dispatch_compensations(state, commands)
        else:
            await saga.fail(describe(handler_err), compensate=True)
            if state.status == SagaStatus.COMPENSATING:  # pyright: ignore[reportUnnecessaryComparison]
                commands = saga.collect_commands()
                await self._dispatch_compensations(state, commands)
        # Commands queued before the handler raised are not sent (ADR-077;
        # checkpointing them is SAGA-12).
        saga.collect_commands_before_compensation()
        await self.repository.save(state)

    async def _dispatch_forward_commands(
        self,
        saga: Saga[Any],
        state: SagaState,
        commands: list[Command[Any]],
        event: DomainEvent | None,
    ) -> UUID:
        """Trace, persist, and dispatch forward (non-compensation) commands.

        Propagates tracing IDs, appends to ``pending_commands`` for
        crash recovery, dispatches one-by-one, and clears pending on
        success.  Returns the saga state id.
        """
        traced_commands = [
            self._trace_command(
                cmd,
                state,
                causation_id=event.event_id if event is not None else cmd.causation_id,
            )
            for cmd in commands
        ]

        # Save pending (recovery checkpoint) before dispatching.
        for cmd in traced_commands:
            state.pending_commands.append(_serialize_command_for_pending(cmd))
        if traced_commands:
            await self.repository.save(state)

        # Dispatch + save per-command for crash recovery.
        if traced_commands:
            try:
                await self._dispatch_and_persist_commands(state, traced_commands)
            except Exception as dispatch_err:
                logger.error(
                    "Saga %s stalled during dispatch: %s",
                    state.id,
                    describe(_failure_of(dispatch_err)),
                )
                # The handler's own failure, described by type only (ADR-071).
                saga.suspend(
                    reason=f"Dispatch failed: {describe(_failure_of(dispatch_err))}"
                )
                state.retry_count += 1
                await self.repository.save(state)
                raise

        # Remove only the batch we just dispatched — preserve older
        # undispatched entries that may remain from a prior failed round.
        if traced_commands:
            batch_start = len(state.pending_commands) - len(traced_commands)
            state.pending_commands = state.pending_commands[:batch_start]
        state.touch()
        await self.repository.save(state)
        return state.id

    async def _process_saga(
        self,
        saga_class: type[Saga[Any]],
        correlation_id: UUID,
        event: DomainEvent | None = None,
    ) -> UUID | None:
        """Load or create a saga instance, pass the event, persist state.

        Returns the saga id on success, ``None`` if skipped.
        """
        saga_type_name = saga_class.__name__
        state = await self.repository.find_by_correlation_id(
            correlation_id, saga_type=saga_type_name
        )

        causation_id = event.event_id if event is not None else None

        if state is None:
            state = await self._create_initial_state(
                saga_class,
                saga_type_name,
                correlation_id,
                causation_id=causation_id,
            )

        if state.is_terminal:
            return None

        # Guard: if retries are exhausted, transition to FAILED immediately.
        if _retries_exhausted(state):
            state.status = SagaStatus.FAILED
            state.error = "Retry limit exceeded"
            state.failed_at = datetime.now(UTC)
            state.touch()
            await self.repository.save(state)
            return state.id

        saga = saga_class(state)

        if state.status == SagaStatus.SUSPENDED:
            if event is not None and not saga.should_resume(event):
                logger.debug(
                    "Saga %s remains suspended — should_resume() rejected event %s",
                    state.id,
                    type(event).__name__,
                )
                return state.id
            saga.resume()

        if event is not None:
            # Update causation_id to track the last event that caused
            # a state change.
            state.causation_id = event.event_id
            try:
                await saga.handle(event)
            except Exception as handler_err:
                # Mark the event as processed even on failure to prevent
                # wasteful re-delivery of the same failing event.
                if not state.is_event_processed(event.event_id):
                    state.mark_event_processed(event.event_id)
                await self._handle_event_error(saga, state, handler_err)

        commands = saga.collect_commands()

        # Compensation dispatch path: the forward commands queued before
        # compensation began go first (ADR-077).
        if state.status == SagaStatus.COMPENSATING:
            await self._dispatch_compensations(
                state,
                commands,
                forward=saga.collect_commands_before_compensation(),
                causation_id=causation_id,
            )
            await self.repository.save(state)
            return state.id

        # Normal (forward) dispatch path.
        return await self._dispatch_forward_commands(saga, state, commands, event)

    # ── Public API ───────────────────────────────────────────────────

    async def handle(self, event: DomainEvent) -> None:
        """Route an event to all registered sagas (event-driven choreography)."""
        event_type = type(event)
        saga_classes = self.registry.get_sagas_for_event(event_type)
        if not saga_classes:
            return

        correlation_id = event.correlation_id
        if not correlation_id:
            logger.warning(
                "Event %s has no correlation_id — cannot route to saga",
                event_type.__name__,
            )
            return

        for saga_class in saga_classes:
            try:
                await self._process_saga(saga_class, correlation_id, event=event)
            except Exception:
                logger.exception(
                    "Saga %s failed for event %s",
                    saga_class.__name__,
                    event_type.__name__,
                )

    async def start_saga(
        self,
        saga_class: type[Saga[Any]],
        initial_event: DomainEvent,
        correlation_id: UUID | None = None,
    ) -> UUID | None:
        """Start (or continue) a saga for the given correlation id.

        Explicit orchestration entry point.
        """
        cid = correlation_id or initial_event.correlation_id
        if not cid:
            cid = self._id_generator.generate()
            logger.warning(
                "No correlation_id provided or found on event for saga %s "
                "— generated %s",
                saga_class.__name__,
                cid,
            )
        return await self._process_saga(saga_class, cid, event=initial_event)

    # ── Recovery ─────────────────────────────────────────────────────

    async def _recover_compensating_saga(self, state: SagaState) -> None:
        """Re-dispatch compensation commands for a stalled COMPENSATING saga."""
        saga_class = self.registry.get_saga_type(state.saga_type)
        if saga_class is None:
            logger.warning(
                "Unknown saga type %s — skipping compensation recovery",
                state.saga_type,
            )
            return

        if state.compensation_stack:
            saga = saga_class(state)
            await saga.execute_compensations()
            commands = saga.collect_commands()
            if commands:
                await self._dispatch_compensations(state, commands)
        else:
            state.status = SagaStatus.COMPENSATED
            state.touch()
        await self.repository.save(state)

    async def _recover_failed_saga(self, state: SagaState) -> None:
        """Fail a saga that has exceeded its max retry count."""
        saga_class = self.registry.get_saga_type(state.saga_type)
        if saga_class is None:
            logger.warning(
                "Unknown saga type %s — skipping failure recovery",
                state.saga_type,
            )
            return

        saga = saga_class(state)
        await saga.fail("Max retries exceeded during recovery", compensate=True)
        if state.status == SagaStatus.COMPENSATING:
            commands = saga.collect_commands()
            await self._dispatch_compensations(state, commands)
        await self.repository.save(state)
        logger.warning(
            "Saga %s failed: max retries (%d) exceeded",
            state.id,
            state.max_retries,
        )

    async def _redispatch_undispatched(
        self,
        saga: Saga[Any],
        undispatched: list[tuple[int, dict[str, Any]]],
    ) -> bool:
        """Re-dispatch the undispatched pending commands of one saga.

        A command that cannot be hydrated (a renamed module or class) is
        not sent and stays in ``pending_commands``; the saga is suspended
        with the command's type as the reason, and the attempt counts as a
        failed one (ADR-077, B-84). A dispatch failure propagates.

        Returns ``True`` when every pending command was sent, after which
        ``pending_commands`` is cleared and ``retry_count`` reset.
        """
        state = saga.state
        unhydratable: list[str] = []
        for i, cmd_data in undispatched:
            command_type = str(cmd_data.get("command_type", ""))
            command = hydrate_command(
                module_name=cmd_data.get("module_name", ""),
                command_type=command_type,
                data=cmd_data.get("data", {}),
            )
            if command is None:
                logger.error(
                    "Could not hydrate command %s for saga %s; it stays pending",
                    command_type,
                    state.id,
                )
                unhydratable.append(command_type)
                continue

            traced = self._trace_command(command, state)
            try:
                await self.command_bus.dispatch(traced)
            except Exception:
                # recover_pending_sagas already counted this attempt.
                await self.repository.save(state)
                raise

            state.pending_commands[i]["dispatched"] = True
            await self.repository.save(state)

        if unhydratable:
            saga.suspend(reason=f"Could not hydrate {', '.join(unhydratable)}")
            await self.repository.save(state)
            return False

        # All dispatched — clean up.
        state.pending_commands.clear()
        state.retry_count = 0
        state.touch()
        await self.repository.save(state)
        return True

    async def _recover_saga(
        self, saga_class: type[Saga[Any]], state: SagaState
    ) -> bool:
        """Recover one stalled saga; return whether it was recovered."""
        if state.status == SagaStatus.COMPENSATING:
            await self._recover_compensating_saga(state)
            return True

        undispatched = [
            (i, cmd_data)
            for i, cmd_data in enumerate(state.pending_commands)
            if not cmd_data.get("dispatched", False)
        ]

        if not undispatched:
            state.pending_commands.clear()
            state.retry_count = 0
            await self.repository.save(state)
            return True

        if _retries_exhausted(state):
            await self._recover_failed_saga(state)
            return True

        state.retry_count += 1
        await self.repository.save(state)
        return await self._redispatch_undispatched(saga_class(state), undispatched)

    async def recover_pending_sagas(self, limit: int = 10) -> list[UUID]:
        """Re-dispatch undispatched commands for stalled sagas.

        Finds the stalled sagas (pending commands, or ``COMPENSATING``),
        re-dispatches the pending commands that were not yet dispatched,
        and finishes interrupted compensations.

        Respects ``SagaState.max_retries``, the number of retries allowed
        after the first attempt: once ``retry_count`` exceeds it, the saga
        is failed (with compensation) instead of retried. ``None`` allows
        any number (ADR-077).

        On each recovery attempt ``retry_count`` is incremented once; on
        success it is reset to ``0`` so a future stall gets a fresh
        count. A pending command that cannot be hydrated stays pending
        and the saga is suspended (ADR-077).

        One saga's failure does not stop the others: it is logged at
        ERROR with the saga id and the exception's type only (ADR-071),
        and recovery continues with the next saga.

        Returns:
            The ids of the sagas not recovered in this run: a dispatch
            failed, a command could not be hydrated, or the saga type is
            not registered. Empty when every stalled saga was recovered.
        """
        not_recovered: list[UUID] = []
        stalled = await self.repository.find_stalled_sagas(limit)
        for state in stalled:
            saga_class = self.registry.get_saga_type(state.saga_type)
            if saga_class is None:
                logger.warning(
                    "Unknown saga type %s — skipping recovery",
                    state.saga_type,
                )
                not_recovered.append(state.id)
                continue
            try:
                recovered = await self._recover_saga(saga_class, state)
            except Exception as exc:
                logger.error(
                    "Recovery failed for saga %s: %s",
                    state.id,
                    describe(_failure_of(exc)),
                )
                recovered = False
            if not recovered:
                not_recovered.append(state.id)
        return not_recovered

    async def process_timeouts(self, limit: int = 10) -> None:
        """Process suspended sagas whose timeout has expired.

        For each expired saga:

        1. Resolve the saga class and instantiate it.
        2. Call ``saga.on_timeout()`` (overridable hook).
        3. If the handler raises, fail the saga.
        4. If still SUSPENDED after ``on_timeout()``, force-fail.
        5. Dispatch any compensation or forward commands.
        6. Save state.
        """
        expired = await self.repository.find_expired_suspended_sagas(limit)
        for state in expired:
            saga_class = self.registry.get_saga_type(state.saga_type)
            if saga_class is None:
                logger.warning(
                    "Unknown saga type %s — skipping timeout",
                    state.saga_type,
                )
                continue

            saga = saga_class(state)

            try:
                await saga.on_timeout()
            except Exception as exc:
                logger.error(
                    "on_timeout() failed for saga %s: %s",
                    state.id,
                    exc,
                )
                if not state.is_terminal:
                    await saga.fail(f"Timeout handler failed: {describe(exc)}")

            # If on_timeout() didn't resolve the suspension, force-fail.
            if state.status == SagaStatus.SUSPENDED and not state.is_terminal:
                await saga.fail(
                    "Timeout handler did not resolve suspension",
                    compensate=False,
                )

            # Dispatch compensations if triggered, after the forward
            # commands queued before compensation began (ADR-077).
            if state.status == SagaStatus.COMPENSATING:
                commands = saga.collect_commands()
                await self._dispatch_compensations(
                    state,
                    commands,
                    forward=saga.collect_commands_before_compensation(),
                )

            # Dispatch any forward commands queued during on_timeout().
            commands = saga.collect_commands()
            for cmd in commands:
                traced = self._trace_command(cmd, state)
                try:
                    await self.command_bus.dispatch(traced)
                except Exception as cmd_err:
                    logger.error(
                        "Failed to dispatch timeout command for saga %s: %s",
                        state.id,
                        cmd_err,
                    )

            await self.repository.save(state)
