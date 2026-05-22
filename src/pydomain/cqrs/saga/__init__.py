"""Saga support for long-running business processes."""

from pydomain.cqrs.saga.exceptions import (
    SagaConfigurationError,
    SagaError,
    SagaHandlerNotFoundError,
    SagaStateError,
)
from pydomain.cqrs.saga.hydration import hydrate_command
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.pruning import SagaPruningPolicy, StepThresholdPruningPolicy
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.repository import SagaRepository
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import (
    CompensationRecord,
    SagaState,
    SagaStatus,
    StepRecord,
)

__all__ = [
    "CompensationRecord",
    "Saga",
    "SagaConfigurationError",
    "SagaError",
    "SagaHandlerNotFoundError",
    "SagaManager",
    "SagaPruningPolicy",
    "SagaRegistry",
    "SagaRepository",
    "SagaState",
    "SagaStateError",
    "SagaStatus",
    "StepRecord",
    "StepThresholdPruningPolicy",
    "hydrate_command",
]
