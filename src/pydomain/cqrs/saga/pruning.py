"""Saga pruning policy — pluggable strategy for automated saga state pruning.

Analogous to :class:`~pydomain.es.snapshot.SnapshotPolicy`, this module
provides a Protocol and a concrete threshold-based implementation for
deciding when and how to prune saga history.

The policy is evaluated by :class:`~pydomain.cqrs.saga.manager.SagaManager`
after processing each event.  If the policy recommends pruning, the manager
calls :meth:`~pydomain.cqrs.saga.state.SagaState.prune_history` with the
policy's configured parameters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydomain.cqrs.saga.state import SagaState


@runtime_checkable
class SagaPruningPolicy(Protocol):
    """Decides whether a saga's history should be pruned.

    Implementations receive the saga type name and current state, and return
    ``True`` if pruning is recommended.  The pruning itself is delegated to
    :meth:`~pydomain.cqrs.saga.state.SagaState.prune_history`, using the
    concrete policy's configured parameters (``keep_last_n_steps``,
    ``keep_last_n_events``).
    """

    @property
    def keep_last_n_steps(self) -> int: ...

    @property
    def keep_last_n_events(self) -> int | None: ...

    def should_prune(self, saga_type: str, state: SagaState) -> bool:
        """Return ``True`` if the saga history should be pruned now.

        Parameters
        ----------
        saga_type:
            The saga class name (e.g. ``"OrderSaga"``).
        state:
            The current saga state.

        Returns
        -------
        bool
            ``True`` if pruning should be performed.
        """
        ...


def _build_no_prune_statuses() -> set[str]:
    """Build the set of saga status *values* where pruning must never happen.

    Deferred construction avoids importing SagaStatus at module level,
    preventing circular imports between this module and state.py.
    """
    from pydomain.cqrs.saga.state import SagaStatus

    return {
        SagaStatus.COMPENSATING.value,  # Compensation stack integrity.
        SagaStatus.SUSPENDED.value,  # May need full history on resume.
        SagaStatus.COMPLETED.value,  # Terminal — no benefit to pruning.
        SagaStatus.FAILED.value,  # Terminal — audit trail preserved.
        SagaStatus.COMPENSATED.value,  # Terminal — audit trail preserved.
    }


# Statuses where pruning must never happen — safety guard.
_NO_PRUNE_STATUSES: set[str] = _build_no_prune_statuses()


class StepThresholdPruningPolicy(SagaPruningPolicy):
    """Prune when ``step_history`` reaches a configured threshold.

    This is the most common pruning strategy: prune when the number of
    accumulated step records exceeds *step_threshold*, keeping only the
    most recent *keep_last_n_steps* entries (and optionally the most
    recent *keep_last_n_events* event IDs).

    **Safety guards**: The policy never recommends pruning for sagas in
    COMPENSATING, SUSPENDED, or terminal states.  Pruning during
    compensation would risk losing compensation records; pruning during
    suspension risks losing context needed on resume; pruning terminal
    sagas provides no benefit.

    Parameters
    ----------
    step_threshold:
        Trigger pruning when ``len(state.step_history) >= step_threshold``.
        Set to ``0`` to prune on every evaluation (for RUNNING sagas with
        at least one step).  Defaults to ``50``.
    keep_last_n_steps:
        How many recent step records to retain after pruning.
        Defaults to ``10``.
    keep_last_n_events:
        How many recent processed event IDs to retain after pruning.
        ``None`` means do not prune event IDs.  Defaults to ``None``.
    """

    def __init__(
        self,
        step_threshold: int = 50,
        keep_last_n_steps: int = 10,
        keep_last_n_events: int | None = None,
    ) -> None:
        if step_threshold < 0:
            raise ValueError("step_threshold must be >= 0")
        if keep_last_n_steps < 0:
            raise ValueError("keep_last_n_steps must be >= 0")
        if keep_last_n_events is not None and keep_last_n_events < 0:
            raise ValueError("keep_last_n_events must be >= 0")
        self._step_threshold = step_threshold
        self._keep_last_n_steps = keep_last_n_steps
        self._keep_last_n_events = keep_last_n_events

    @property
    def step_threshold(self) -> int:
        """The step count threshold that triggers pruning."""
        return self._step_threshold

    @property
    def keep_last_n_steps(self) -> int:
        """Number of recent step records to retain after pruning."""
        return self._keep_last_n_steps

    @property
    def keep_last_n_events(self) -> int | None:
        """Number of recent event IDs to retain, or ``None`` to skip."""
        return self._keep_last_n_events

    def should_prune(self, saga_type: str, state: SagaState) -> bool:
        # Safety guard: never prune sagas in critical or terminal states.
        if state.status.value in _NO_PRUNE_STATUSES:
            return False

        if self._step_threshold == 0:
            return len(state.step_history) > 0

        return len(state.step_history) >= self._step_threshold
