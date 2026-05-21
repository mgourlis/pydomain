"""Saga base class — explicit state machine for long-running processes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from pydomain.cqrs.saga.exceptions import (
    SagaConfigurationError,
    SagaHandlerNotFoundError,
)
from pydomain.cqrs.saga.hydration import hydrate_command
from pydomain.cqrs.saga.state import (
    CompensationRecord,
    SagaState,
    SagaStatus,
)

if TYPE_CHECKING:
    from pydomain.cqrs.commands import Command
    from pydomain.ddd.domain_event import DomainEvent

logger = logging.getLogger("pydomain.saga")


class Saga[S: SagaState]:
    """Base class for Sagas / Process Managers.

    Two styles for event handling:

    *Command mapper* — maps an event directly to a command dispatch::

        self.on(OrderCreated,
                send=lambda e: ReserveItems(order_id=e.order_id),
                step="reserving",
                compensate=lambda e: CancelReservation(order_id=e.order_id))

    *Handler* — for complex logic requiring conditional dispatch::

        self.on(OrderCreated, handler=self.handle_order_created)

    Hand-written saga subclasses **must** set the class-level
    ``listens_to`` to the list of event types they handle.
    """

    state_class: ClassVar[type[SagaState]] = SagaState
    listens_to: ClassVar[list[type[DomainEvent]]] = []

    def __init__(
        self,
        state: S,
    ) -> None:
        self.state: S = state
        self._commands_to_dispatch: list[Command[Any]] = []
        self._event_handlers: dict[
            type[DomainEvent],
            Callable[[DomainEvent], None] | Callable[[DomainEvent], Awaitable[None]],
        ] = {}

    # ── Class-level event declaration ───────────────────────────────

    @classmethod
    def listened_events(cls) -> list[type[DomainEvent]]:
        """Return event types this saga handles (from ``listens_to``)."""
        if cls is Saga:
            return []
        if cls.listens_to:
            return list(cls.listens_to)
        return []

    # ── Public entry point ──────────────────────────────────────────

    async def handle(self, event: DomainEvent) -> None:
        """Idempotent entry point — skips already-processed events."""
        if self.state.is_terminal:
            logger.debug(
                "Saga %s is in terminal state %s — ignoring event %s",
                self.state.id,
                self.state.status,
                type(event).__name__,
            )
            return

        if self.state.is_event_processed(event.event_id):
            return

        # Transition to RUNNING on first handled event.
        if self.state.status == SagaStatus.PENDING:
            self.state.status = SagaStatus.RUNNING

        result = self._handle_event(event)
        if isawaitable(result):
            await result

        self.state.mark_event_processed(event.event_id)
        self.state.record_step(
            step_name=self.state.current_step,
            event_type=type(event).__name__,
            causation_id=event.event_id,
        )

    async def _handle_event(self, event: DomainEvent) -> None:
        """Dispatch to registered handlers (from ``on()``) or raise.

        Override in subclasses for imperative dispatch with
        ``match``/``case`` or ``if``/``elif``.
        """
        handler = self._event_handlers.get(type(event))
        if handler is not None:
            result = handler(event)
            if isawaitable(result):
                await result
        else:
            raise SagaHandlerNotFoundError(
                f"No handler registered for {type(event).__name__}. "
                f"Either override _handle_event() or register handlers with on()."
            )

    # ── Event → Command mapping (declarative) ──────────────────────

    def on(
        self,
        event_type: type[DomainEvent],
        handler: Callable[[DomainEvent], None]
        | Callable[[DomainEvent], Awaitable[None]]
        | None = None,
        *,
        send: Callable[[DomainEvent], Command[Any]] | None = None,
        step: str | None = None,
        compensate: Callable[[DomainEvent], Command[Any]] | None = None,
        compensate_description: str = "",
        complete: bool = False,
        suspend: bool = False,
        suspend_reason: str | None = None,
        suspend_timeout: timedelta | None = None,
    ) -> None:
        """Register an event mapping — either a handler or a command dispatch.

        Args:
            event_type: The domain event class to handle.
            handler: Custom handler callable. Mutually exclusive with *send*.
            send: Command factory — receives the event, returns a
                ``Command`` to dispatch through the command bus.
            step: Set ``current_step`` on the saga state when this event
                is received.
            compensate: Compensation command factory — if provided, a
                compensating command is pushed onto the stack.
            compensate_description: Description for the compensation record.
            complete: If ``True``, mark the saga as *COMPLETED* after
                processing this event.
            suspend: If ``True``, suspend the saga after processing this
                event (human-in-the-loop pattern).
            suspend_reason: Optional reason for suspension (for audit).
            suspend_timeout: Optional timeout for the suspension.
                Auto-expires if set.
        """
        if handler is not None and send is not None:
            raise SagaConfigurationError(
                "Cannot provide both 'handler' and 'send' — pick one."
            )
        if handler is None and send is None:
            raise SagaConfigurationError("Must provide either 'handler' or 'send'.")
        if complete and suspend:
            raise SagaConfigurationError(
                "Cannot set both 'complete' and 'suspend' — "
                "a saga step cannot complete and suspend simultaneously."
            )

        if handler is not None:
            self._event_handlers[event_type] = handler
        else:
            # Build a handler from the command-mapper parameters.
            # Each parameter is captured directly by the closure —
            # no local aliases needed since on() is called once per
            # event type, not inside a loop.
            #
            # send is guaranteed non-None here: the two validation
            # guards above ensure either handler or send is provided,
            # but not both and not neither.  In this else-branch
            # handler is None, so send must be non-None.
            _send_fn: Callable[[DomainEvent], Command[Any]] = send  # type: ignore[assignment]
            _comp_fn: Callable[[DomainEvent], Command[Any]] | None = compensate

            async def _mapped_handler(
                evt: DomainEvent,
                *,
                _send: Callable[[DomainEvent], Command[Any]] = _send_fn,
                _step: str | None = step,
                _compensate: Callable[[DomainEvent], Command[Any]] | None = _comp_fn,
                _comp_desc: str = compensate_description,
                _complete: bool = complete,
                _suspend: bool = suspend,
                _suspend_timeout: timedelta | None = suspend_timeout,
            ) -> None:
                if _step is not None:
                    self.state.current_step = _step
                command = _send(evt)
                self.dispatch(command)
                if _compensate is not None:
                    comp_cmd = _compensate(evt)
                    self.add_compensation(comp_cmd, _comp_desc)
                if _suspend:
                    self.suspend(
                        reason=suspend_reason
                        or f"Suspended at step {_step or 'unknown'}",
                        timeout=_suspend_timeout,
                    )
                elif _complete:
                    self.complete()

            self._event_handlers[event_type] = _mapped_handler  # pyright: ignore[reportArgumentType]

    # ── Command dispatch ────────────────────────────────────────────

    def dispatch(self, command: Command[Any]) -> None:
        """Queue a command for dispatch by the ``SagaManager``."""
        self._commands_to_dispatch.append(command)

    def collect_commands(self) -> list[Command[Any]]:
        """Return all queued commands and clear the internal list."""
        cmds = list(self._commands_to_dispatch)
        self._commands_to_dispatch.clear()
        return cmds

    # ── Lifecycle transitions ───────────────────────────────────────

    def complete(self) -> None:
        """Mark the saga as successfully completed."""
        self.state.status = SagaStatus.COMPLETED
        self.state.completed_at = datetime.now(UTC)
        self.state.compensation_stack.clear()
        self.state.touch()

    async def fail(self, reason: str, *, compensate: bool = True) -> None:
        """Mark the saga as failed and optionally trigger compensation.

        Args:
            reason: Human-readable failure description.
            compensate: If ``True`` (default) and there are compensating
                commands on the stack, ``execute_compensations()`` is called.
        """
        self.state.error = reason
        self.state.failed_at = datetime.now(UTC)

        if compensate and self.state.compensation_stack:
            await self.execute_compensations()
        else:
            self.state.status = SagaStatus.FAILED
            self.state.touch()

    def suspend(
        self,
        reason: str,
        timeout: timedelta | None = None,
    ) -> None:
        """Suspend the saga, optionally with a timeout for auto-expiry."""
        self.state.status = SagaStatus.SUSPENDED
        self.state.suspended_at = datetime.now(UTC)
        self.state.suspension_reason = reason
        if timeout is not None:
            self.state.timeout_at = datetime.now(UTC) + timeout
        else:
            self.state.timeout_at = None
        self.state.touch()

    def resume(self) -> None:
        """Resume a previously suspended saga."""
        if self.state.status != SagaStatus.SUSPENDED:
            logger.warning(
                "Attempted to resume saga %s which is %s, not SUSPENDED",
                self.state.id,
                self.state.status,
            )
            return
        self.state.status = SagaStatus.RUNNING
        self.state.suspended_at = None
        self.state.suspension_reason = None
        self.state.timeout_at = None
        self.state.touch()

    def should_resume(self, event: DomainEvent) -> bool:
        """Determine whether a suspended saga should resume for the given event.

        Override in subclasses to filter which events can resume a
        suspended saga.  The default implementation returns ``True``
        for all events (backward-compatible behaviour).

        Args:
            event: The incoming domain event.

        Returns:
            ``True`` if the saga should be resumed, ``False`` to keep
            it suspended.
        """
        return True

    async def on_timeout(self) -> None:
        """Called when a suspended saga's timeout expires.

        Default behavior: fail with reason including suspension context.
        Subclasses override for custom recovery (escalate, retry,
        partial complete).
        """
        reason = "Saga timed out while suspended"
        if self.state.suspension_reason:
            reason += f" for: {self.state.suspension_reason}"
        await self.fail(reason)

    # ── Compensation ────────────────────────────────────────────────

    def add_compensation(
        self,
        command: Command[Any],
        description: str = "",
    ) -> None:
        """Push a compensating command onto the LIFO stack."""
        record = CompensationRecord(
            command_type=type(command).__name__,
            data=command.model_dump(),
            description=description,
            module_name=type(command).__module__,
        )
        self.state.compensation_stack.append(record)

    async def execute_compensations(self) -> None:
        """Pop compensating records and hydrate them for dispatch.

        Sets status to ``COMPENSATING``, hydrates each record into a
        live ``Command``, and queues them via ``dispatch()``.  The
        ``SagaManager`` picks them up via ``collect_commands()`` and
        dispatches them through the command bus.

        Any previously queued forward commands are discarded so they
        don't pollute the compensation dispatch path.

        Hydration failures are recorded in ``state.failed_compensations``.
        The manager transitions the saga to ``COMPENSATED`` or ``FAILED``
        after dispatching all commands.
        """
        self.state.status = SagaStatus.COMPENSATING
        self.state.touch()

        # Discard any forward commands queued before compensation.
        # Only compensation commands should be dispatched.
        self._commands_to_dispatch.clear()

        while self.state.compensation_stack:
            record = self.state.compensation_stack.pop()  # LIFO
            command = hydrate_command(
                module_name=record.module_name,
                command_type=record.command_type,
                data=record.data,
            )
            if command is not None:
                self.dispatch(command)
            else:
                self.state.record_failed_compensation(
                    command_type=record.command_type,
                    data=record.data,
                    module_name=record.module_name,
                    error=f"Could not hydrate from {record.module_name}",
                )
                logger.error(
                    "Could not hydrate compensation command %s from module %s",
                    record.command_type,
                    record.module_name,
                )
