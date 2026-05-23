"""SagaManager — orchestrates saga lifecycle (load → handle → save → dispatch)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.saga.hydration import hydrate_command
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.id_generator import Uuid7Generator

if TYPE_CHECKING:
    from pydomain.cqrs.command_bus import CommandBus
    from pydomain.cqrs.commands import Command
    from pydomain.ddd.domain_event import DomainEvent

    from .registry import SagaRegistry
    from .repository import SagaRepository
    from .saga import Saga

logger = logging.getLogger("pydomain.saga")


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
    5. Dispatches pending commands via the ``CommandBus``.
    """

    def __init__(
        self,
        repository: SagaRepository,
        registry: SagaRegistry,
        command_bus: CommandBus,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.command_bus = command_bus
        self._id_generator = Uuid7Generator()

    # ── Event Dispatcher Integration ─────────────────────────────────

    def bind_to(self, event_dispatcher: Any) -> None:
        """Auto-register this manager as an event handler with the dispatcher.

        Reads all event types from the :class:`SagaRegistry` and registers
        :meth:`handle` for each one.  The *event_dispatcher* must provide a
        ``register_event(event_type, handler)`` method (e.g.
        :class:`~pydomain.infrastructure.message_bus.MessageBus`).

        Parameters
        ----------
        event_dispatcher:
            Any object with a ``register_event(event_type, handler)`` method.
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
        self, state: SagaState, commands: list[Command[Any]]
    ) -> None:
        """Dispatch compensation commands and transition to terminal state.

        Propagates tracing IDs (correlation_id stays constant,
        causation_id = saga state id) and dispatches each command
        individually.  Failures are recorded in
        ``state.failed_compensations``.

        After dispatching, transitions the saga to ``COMPENSATED``
        if no failures were recorded (hydration or dispatch),
        or ``FAILED`` otherwise.
        """
        for cmd in commands:
            traced = self._trace_command(cmd, state)
            try:
                await self.command_bus.dispatch(traced)
            except Exception as comp_err:
                state.record_failed_compensation(
                    command_type=type(traced).__name__,
                    data=traced.model_dump(),
                    module_name=type(traced).__module__,
                    error=str(comp_err),
                )
                logger.error(
                    "Compensation command %s failed for saga %s: %s",
                    type(traced).__name__,
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
        """
        if state.status == SagaStatus.COMPENSATING:
            commands = saga.collect_commands()
            await self._dispatch_compensations(state, commands)
        else:
            await saga.fail(str(handler_err), compensate=True)
            if state.status == SagaStatus.COMPENSATING:  # pyright: ignore[reportUnnecessaryComparison]
                commands = saga.collect_commands()
                await self._dispatch_compensations(state, commands)
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
                    dispatch_err,
                )
                cause = (
                    dispatch_err.__cause__
                    if isinstance(dispatch_err, CommandExecutionError)
                    else dispatch_err
                )
                saga.suspend(reason=f"Dispatch failed: {cause}")
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
        if state.retry_count >= state.max_retries and state.max_retries > 0:
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

        # Compensation dispatch path.
        if state.status == SagaStatus.COMPENSATING:
            await self._dispatch_compensations(state, commands)
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
        state: SagaState,
        undispatched: list[tuple[int, dict[str, Any]]],
    ) -> None:
        """Attempt to re-dispatch individual undispatched pending commands."""
        for i, cmd_data in undispatched:
            command = hydrate_command(
                module_name=cmd_data.get("module_name", ""),
                command_type=cmd_data.get("command_type", ""),
                data=cmd_data.get("data", {}),
            )
            if command is None:
                logger.error(
                    "Could not hydrate command %s for saga %s — skipping",
                    cmd_data.get("command_type"),
                    state.id,
                )
                continue

            traced = self._trace_command(command, state)
            try:
                await self.command_bus.dispatch(traced)
            except Exception as dispatch_err:
                logger.error(
                    "Re-dispatch failed for saga %s: %s",
                    state.id,
                    dispatch_err,
                )
                state.retry_count += 1
                await self.repository.save(state)
                raise

            state.pending_commands[i]["dispatched"] = True
            await self.repository.save(state)

        # All dispatched — clean up.
        state.pending_commands.clear()
        state.retry_count = 0
        state.touch()
        await self.repository.save(state)

    async def recover_pending_sagas(self, limit: int = 10) -> None:
        """Re-dispatch undispatched commands for stalled sagas.

        Finds sagas with non-empty ``pending_commands`` and attempts
        to re-dispatch the ones that were not yet dispatched.

        Respects ``SagaState.max_retries``: if ``retry_count >=
        max_retries``, the saga is failed (terminal state) and no
        further recovery is attempted.

        On each recovery attempt ``retry_count`` is incremented; on
        success it is reset to ``0`` so a future stall gets a fresh
        count.
        """
        stalled = await self.repository.find_stalled_sagas(limit)
        for state in stalled:
            saga_class = self.registry.get_saga_type(state.saga_type)
            if saga_class is None:
                logger.warning(
                    "Unknown saga type %s — skipping recovery",
                    state.saga_type,
                )
                continue

            if state.status == SagaStatus.COMPENSATING:
                await self._recover_compensating_saga(state)
                continue

            undispatched = [
                (i, cmd_data)
                for i, cmd_data in enumerate(state.pending_commands)
                if not cmd_data.get("dispatched", False)
            ]

            if not undispatched:
                state.pending_commands.clear()
                state.retry_count = 0
                await self.repository.save(state)
                continue

            if state.retry_count >= state.max_retries:
                await self._recover_failed_saga(state)
                continue

            state.retry_count += 1
            await self.repository.save(state)
            await self._redispatch_undispatched(state, undispatched)

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
                    await saga.fail(f"Timeout handler failed: {exc}")

            # If on_timeout() didn't resolve the suspension, force-fail.
            if state.status == SagaStatus.SUSPENDED and not state.is_terminal:
                await saga.fail(
                    "Timeout handler did not resolve suspension",
                    compensate=False,
                )

            # Dispatch compensations if triggered.
            if state.status == SagaStatus.COMPENSATING:
                commands = saga.collect_commands()
                await self._dispatch_compensations(state, commands)

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
