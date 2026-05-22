from __future__ import annotations

import pydomain
import pydomain.cqrs
import pydomain.es
import pydomain.testing


class TestEsSubmoduleExports:
    """Verify ``pydomain.es.__all__`` contains every expected name."""

    EXPECTED_ES = [
        "CheckpointStore",
        "DuplicateCommandError",
        "EventSourcedAggregateRoot",
        "EventSourcedProjection",
        "EventSourcedRepository",
        "EventStore",
        "EventStream",
        "EventUpcaster",
        "Snapshot",
        "SnapshotPolicy",
        "SnapshotStore",
        "SnapshotThresholdPolicy",
        "StreamNotFoundError",
        "UpcastError",
        "UpcasterRegistry",
    ]

    def test_es_has_all_expected_names(self) -> None:
        for name in self.EXPECTED_ES:
            assert name in pydomain.es.__all__, (
                f"Expected {name!r} in pydomain.es.__all__"
            )

    def test_es_has_no_extra_names(self) -> None:
        expected = set(self.EXPECTED_ES)
        extra = set(pydomain.es.__all__) - expected
        assert not extra, (
            f"pydomain.es.__all__ contains unexpected names: {sorted(extra)}. "
            f"If these are intentional, add them to EXPECTED_ES."
        )


class TestTestingSubmoduleExports:
    """Verify ``pydomain.testing.__all__`` contains every expected name."""

    EXPECTED_TESTING = [
        "FakeCheckpointStore",
        "FakeEventStore",
        "FakeLockProvider",
        "FakeProcessedCommandStore",
        "FakeRepository",
        "FakeSagaRepository",
        "FakeSnapshotStore",
        "FakeUnitOfWork",
        "InMemoryMessageBroker",
        "InMemoryProjectionStore",
        "ProjectionStore",
    ]

    def test_testing_has_all_expected_names(self) -> None:
        for name in self.EXPECTED_TESTING:
            assert name in pydomain.testing.__all__, (
                f"Expected {name!r} in pydomain.testing.__all__"
            )

    def test_testing_has_no_extra_names(self) -> None:
        expected = set(self.EXPECTED_TESTING)
        extra = set(pydomain.testing.__all__) - expected
        assert not extra, (
            f"pydomain.testing.__all__ contains unexpected names: {sorted(extra)}. "
            f"If these are intentional, add them to EXPECTED_TESTING."
        )


class TestTopLevelDddExports:
    """Verify DDD names in ``pydomain.__all__``."""

    EXPECTED_DDD = [
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
        "Specification",
        "SpecificationError",
        "Uuid7Generator",
        "ValueObject",
    ]

    def test_top_level_has_all_ddd_names(self) -> None:
        for name in self.EXPECTED_DDD:
            assert name in pydomain.__all__, f"Expected {name!r} in pydomain.__all__"


class TestTopLevelCqrsExports:
    """Verify CQRS names in ``pydomain.__all__``."""

    EXPECTED_CQRS = [
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

    def test_top_level_has_all_cqrs_names(self) -> None:
        for name in self.EXPECTED_CQRS:
            assert name in pydomain.__all__, (
                f"Expected CQRS name {name!r} in pydomain.__all__"
            )


class TestTopLevelEsExports:
    """Verify ES names in ``pydomain.__all__``.

    ``Projection`` from ``pydomain.es`` is intentionally *not* re-exported
    at the top level to avoid shadowing ``Projection`` from
    ``pydomain.cqrs`` (the Protocol). ``Subscription`` is also excluded
    because it is an internal dataclass consumed via ``SubscriptionRunner``.
    """

    EXPECTED_ES = [
        "CheckpointStore",
        "DuplicateCommandError",
        "EventSourcedAggregateRoot",
        "EventSourcedRepository",
        "EventStore",
        "EventStream",
        "StreamNotFoundError",
    ]

    def test_top_level_has_all_es_names(self) -> None:
        for name in self.EXPECTED_ES:
            assert name in pydomain.__all__, (
                f"Expected ES name {name!r} in pydomain.__all__"
            )

    def test_top_level_projection_is_cqrs_not_es(self) -> None:
        """Top-level ``Projection`` must be the CQRS Protocol, not the ES ABC."""
        assert pydomain.Projection is pydomain.cqrs.Projection, (
            "pydomain.Projection must resolve to the CQRS Protocol. "
            "If pydomain.es.Projection was accidentally added to the top-level "
            "import block, late-binding would overwrite the CQRS version."
        )


class TestTopLevelInfrastructureExports:
    """Verify infrastructure names in ``pydomain.__all__``."""

    EXPECTED_INFRA = [
        "AbstractUnitOfWork",
        "SubscriptionRunner",
        "UnitOfWork",
    ]

    def test_top_level_has_all_infrastructure_names(self) -> None:
        for name in self.EXPECTED_INFRA:
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


class TestTopLevelExhaustive:
    """Verify ``pydomain.__all__`` matches the union of all category lists exactly.

    This catches both missing names (someone added an export but forgot the
    test) and extra names (someone removed a name from the expected list but
    left it in ``__all__``).
    """

    EXPECTED_SAGA = [
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
    ]

    def test_top_level_all_matches_expected_union(self) -> None:
        expected = sorted(
            set(TestTopLevelDddExports.EXPECTED_DDD)
            | set(TestTopLevelCqrsExports.EXPECTED_CQRS)
            | set(TestTopLevelEsExports.EXPECTED_ES)
            | set(TestTopLevelInfrastructureExports.EXPECTED_INFRA)
            | set(self.EXPECTED_SAGA)
        )
        actual = sorted(pydomain.__all__)
        assert actual == expected, (
            f"pydomain.__all__ does not match the union of expected names.\n"
            f"Missing from tests: {sorted(set(expected) - set(actual))}\n"
            f"Extra in __all__: {sorted(set(actual) - set(expected))}"
        )
