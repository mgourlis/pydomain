from __future__ import annotations

import pydomain
import pydomain.es
import pydomain.testing


class TestEsSubmoduleExports:
    """Verify ``pydomain.es.__all__`` contains every expected name."""

    def test_es_exports(self) -> None:
        expected = [
            "CheckpointStore",
            "EventSourcedAggregateRoot",
            "EventSourcedRepository",
            "EventStore",
            "EventStream",
            "Projection",
            "StreamAlreadyExistsError",
            "StreamNotFoundError",
            "Subscription",
            "SubscriptionRunner",
        ]
        for name in expected:
            assert name in pydomain.es.__all__, (
                f"Expected {name!r} in pydomain.es.__all__"
            )


class TestTestingSubmoduleExports:
    """Verify ``pydomain.testing.__all__`` contains every expected name."""

    def test_testing_exports(self) -> None:
        expected = [
            "FakeCheckpointStore",
            "FakeEventSourcedRepository",
            "FakeEventStore",
            "FakeLockProvider",
            "FakeProcessedCommandStore",
            "FakeRepository",
            "FakeUnitOfWork",
            "InMemoryMessageBroker",
            "InMemoryProjectionStore",
            "ProjectionStore",
        ]
        for name in expected:
            assert name in pydomain.testing.__all__, (
                f"Expected {name!r} in pydomain.testing.__all__"
            )


class TestTopLevelDddExports:
    """Verify DDD names in ``pydomain.__all__``."""

    def test_top_level_exports_ddd(self) -> None:
        expected = [
            "AggregateNotFoundError",
            "AggregateRoot",
            "AndSpecification",
            "ConcurrencyError",
            "DomainError",
            "DomainEvent",
            "DomainService",
            "Entity",
            "Factory",
            "IdGenerator",
            "NotSpecification",
            "OrSpecification",
            "ReconstitutionFactory",
            "Repository",
            "RepositoryError",
            "Specification",
            "SpecificationError",
            "Uuid7Generator",
            "ValueObject",
        ]
        for name in expected:
            assert name in pydomain.__all__, f"Expected {name!r} in pydomain.__all__"


class TestTopLevelCqrsExports:
    """Verify CQRS names in ``pydomain.__all__``."""

    def test_top_level_exports_cqrs(self) -> None:
        expected = [
            "AggregateLockingBehavior",
            "Command",
            "CommandBus",
            "CommandExecutionError",
            "CommandHandler",
            "CommandResult",
            "CQRSError",
            "DictLockKeyResolver",
            "EmptyCommandResult",
            "HandlerAlreadyRegisteredError",
            "IdempotencyBehavior",
            "IdempotentCommandIgnored",
            "IntegrationEvent",
            "LockKeyResolver",
            "LockProvider",
            "LoggingBehavior",
            "MISSING",
            "MessageContext",
            "MessageKind",
            "MessagePipeline",
            "NextHandler",
            "NoHandlerRegisteredError",
            "PipelineBehavior",
            "ProcessedCommandStore",
            "Projection",
            "ProjectionStore",
            "Query",
            "QueryBus",
            "QueryHandler",
            "QueryResult",
            "ValidationBehavior",
        ]
        for name in expected:
            assert name in pydomain.__all__, (
                f"Expected CQRS name {name!r} in pydomain.__all__"
            )


class TestTopLevelEsExports:
    """Verify ES names in ``pydomain.__all__``.

    ``Projection`` and ``Subscription`` from ``pydomain.es`` are intentionally
    *not* re-exported at the top level to avoid naming conflicts with
    ``Projection`` from ``pydomain.cqrs``.
    """

    def test_top_level_exports_es(self) -> None:
        expected = [
            "CheckpointStore",
            "EventSourcedAggregateRoot",
            "EventSourcedRepository",
            "EventStore",
            "EventStream",
            "StreamAlreadyExistsError",
            "StreamNotFoundError",
            "SubscriptionRunner",
        ]
        for name in expected:
            assert name in pydomain.__all__, (
                f"Expected ES name {name!r} in pydomain.__all__"
            )


class TestTopLevelInfrastructureExports:
    """Verify infrastructure names in ``pydomain.__all__``."""

    def test_top_level_exports_infrastructure(self) -> None:
        expected = [
            "AbstractUnitOfWork",
            "UnitOfWork",
        ]
        for name in expected:
            assert name in pydomain.__all__, (
                f"Expected infrastructure name {name!r} in pydomain.__all__"
            )


class TestTopLevelImportability:
    """Every name in ``pydomain.__all__`` must be importable.

    This catches stale or rotten exports where a name remains listed in
    ``__all__`` but the corresponding ``from ... import`` statement in
    ``pydomain/__init__.py`` has been removed or broken.
    """

    def test_top_level_importability(self) -> None:
        for name in pydomain.__all__:
            imported = getattr(pydomain, name, None)
            assert imported is not None, (
                f"from pydomain import {name} failed -- "
                f"{name!r} is in __all__ but not resolvable"
            )
