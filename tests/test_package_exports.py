"""Exact-match tests for every public ``__all__`` (TEST-07).

Each package's ``__all__`` is compared with the full list of names it must
export, so adding or removing an export fails until the list here changes
with it. ``test_readme_import_paths_resolve`` imports every path the README
shows.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from types import ModuleType

import pytest

import pydomain
import pydomain.cqrs
import pydomain.cqrs.saga
import pydomain.ddd
import pydomain.es
import pydomain.infrastructure
import pydomain.testing
import pydomain.testing.contracts

README = Path(__file__).resolve().parents[1] / "README.md"


def _assert_all_is(module: ModuleType, expected: list[str]) -> None:
    actual = list(module.__all__)
    assert sorted(actual) == sorted(expected), (
        f"{module.__name__}.__all__ differs from the expected names.\n"
        f"Missing: {sorted(set(expected) - set(actual))}\n"
        f"Unexpected: {sorted(set(actual) - set(expected))}"
    )
    assert len(actual) == len(set(actual)), f"{module.__name__}.__all__ repeats a name"


class TestEsSubmoduleExports:
    """Verify ``pydomain.es.__all__`` contains every expected name."""

    EXPECTED_ES = [
        "CheckpointStore",
        "DuplicateCommandError",
        "EventSourcedAggregateRoot",
        "EventSourcedRepository",
        "EventStore",
        "EventStream",
        "EventUpcaster",
        "EventSourcedProjection",
        "RejectStaleSnapshotPolicy",
        "Snapshot",
        "SnapshotPolicy",
        "SnapshotRestoreError",
        "SnapshotSchemaPolicy",
        "SnapshotStore",
        "SnapshotThresholdPolicy",
        "StaleSnapshotError",
        "StreamNotFoundError",
        "UpcastError",
        "UpcasterRegistry",
    ]

    def test_es_has_all_expected_names(self) -> None:
        for name in self.EXPECTED_ES:
            assert name in pydomain.es.__all__, (
                f"Expected {name!r} in pydomain.es.__all__"
            )

    def test_es_all_matches_expected(self) -> None:
        _assert_all_is(pydomain.es, self.EXPECTED_ES)

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
        "FakeOutboxStore",
        "FakeProcessedMessageStore",
        "FakeRepository",
        "FakeSagaRepository",
        "FakeSnapshotStore",
        "FakeUnitOfWork",
        "InMemoryMessageBroker",
        "InMemoryMessageSubscriber",
        "InMemoryProjectionStore",
        "ProjectionStore",
    ]

    def test_testing_has_all_expected_names(self) -> None:
        for name in self.EXPECTED_TESTING:
            assert name in pydomain.testing.__all__, (
                f"Expected {name!r} in pydomain.testing.__all__"
            )

    def test_testing_all_matches_expected(self) -> None:
        _assert_all_is(pydomain.testing, self.EXPECTED_TESTING)

    def test_testing_has_no_extra_names(self) -> None:
        expected = set(self.EXPECTED_TESTING)
        extra = set(pydomain.testing.__all__) - expected
        assert not extra, (
            f"pydomain.testing.__all__ contains unexpected names: {sorted(extra)}. "
            f"If these are intentional, add them to EXPECTED_TESTING."
        )


class TestContractsSubmoduleExports:
    """Verify ``pydomain.testing.contracts.__all__``.

    The suites are exported only from ``pydomain.testing.contracts``, never
    from ``pydomain.testing`` or ``pydomain``, because they import pytest.
    """

    EXPECTED_CONTRACTS = [
        "CheckpointStoreContract",
        "EventStoreContract",
        "LockProviderContract",
        "MessageBrokerContract",
        "MessageSubscriberContract",
        "OutboxStoreContract",
        "ProcessedMessageStoreContract",
        "ProjectionStoreContract",
        "RepositoryContract",
        "SagaRepositoryContract",
        "SnapshotStoreContract",
        "UnitOfWorkContract",
    ]

    def test_contracts_all_matches_expected(self) -> None:
        assert sorted(pydomain.testing.contracts.__all__) == sorted(
            self.EXPECTED_CONTRACTS
        )

    def test_contracts_names_are_importable(self) -> None:
        for name in pydomain.testing.contracts.__all__:
            assert getattr(pydomain.testing.contracts, name, None) is not None, (
                f"{name!r} is in pydomain.testing.contracts.__all__ but not resolvable"
            )

    def test_contracts_are_not_exported_from_testing_or_top_level(self) -> None:
        exported = set(pydomain.testing.__all__) | set(pydomain.__all__)
        assert not exported & set(self.EXPECTED_CONTRACTS)


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
        "EventBus",
        "EventIdempotencyBehavior",
        "HandlerAlreadyRegisteredError",
        "IdempotencyBehavior",
        "IdempotentCommandIgnored",
        "UnitOfWorkAlreadyCommittedError",
        "IntegrationEvent",
        "LockKeyResolver",
        "LockProvider",
        "LockReentryError",
        "LoggingBehavior",
        "MISSING",
        "MessageContext",
        "MessageKind",
        "MessagePipeline",
        "NextHandler",
        "NoHandlerRegisteredError",
        "OutboundEventRegistry",
        "OutboxEntry",
        "OutboxStore",
        "OutboxWriter",
        "PipelineBehavior",
        "ProcessedMessageStore",
        "Projection",
        "ProjectionStore",
        "Query",
        "QueryBus",
        "QueryHandler",
        "QueryResult",
        "ValidationBehavior",
        # The unit of work lives in pydomain.cqrs.unit_of_work.
        "AbstractUnitOfWork",
        "UnitOfWork",
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
        "RejectStaleSnapshotPolicy",
        "SnapshotRestoreError",
        "SnapshotSchemaPolicy",
        "StaleSnapshotError",
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
        "OutboundEventGateway",
        "SubscriptionRunner",
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
        "CommandDispatcher",
        "EventHandlerRegistrar",
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


# ── Subpackages without a class above (TEST-07) ──────────────────────────


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
    "hydrate_command",
]

EXPECTED_CQRS = [
    "AggregateLockingBehavior",
    "CQRSError",
    "Command",
    "CommandBus",
    "CommandExecutionError",
    "CommandHandler",
    "CommandResult",
    "CompensationRecord",
    "DictLockKeyResolver",
    "EmptyCommandResult",
    "EventBus",
    "EventHandler",
    "EventIdempotencyBehavior",
    "HandlerAlreadyRegisteredError",
    "IdempotencyBehavior",
    "IdempotentCommandIgnored",
    "IntegrationEvent",
    "LockKeyResolver",
    "LockProvider",
    "LockReentryError",
    "LoggingBehavior",
    "MISSING",
    "MessageContext",
    "MessageKind",
    "MessagePipeline",
    "NextHandler",
    "NoHandlerRegisteredError",
    "OutboundEventRegistry",
    "OutboxEntry",
    "OutboxStore",
    "OutboxWriter",
    "PipelineBehavior",
    "ProcessedMessageStore",
    "Projection",
    "ProjectionStore",
    "Query",
    "QueryBus",
    "QueryHandler",
    "QueryResult",
    "Saga",
    "SagaConfigurationError",
    "SagaError",
    "SagaHandlerNotFoundError",
    "SagaManager",
    "SagaRegistry",
    "SagaRepository",
    "SagaState",
    "SagaStateError",
    "SagaStatus",
    "StepRecord",
    "UnitOfWork",
    "UnitOfWorkAlreadyCommittedError",
    "ValidationBehavior",
    "hydrate_command",
]

EXPECTED_INFRASTRUCTURE = [
    "Application",
    "EventRegistry",
    "GenericDomainEvent",
    "InboundEventGateway",
    "IntegrationEvent",
    "MessageBroker",
    "MessageBus",
    "MessageSubscriber",
    "OutboundEventGateway",
    "OutboundEventRegistry",
    "OutboxEntry",
    "OutboxStore",
    "OutboxWriter",
    "Subscription",
    "SubscriptionRunner",
    "bootstrap",
]


def test_ddd_all_matches_expected() -> None:
    _assert_all_is(pydomain.ddd, EXPECTED_DDD)


def test_top_level_ddd_names_match_the_ddd_package() -> None:
    assert sorted(TestTopLevelDddExports.EXPECTED_DDD) == sorted(EXPECTED_DDD)


def test_cqrs_all_matches_expected() -> None:
    _assert_all_is(pydomain.cqrs, EXPECTED_CQRS)


def test_saga_all_matches_expected() -> None:
    _assert_all_is(pydomain.cqrs.saga, EXPECTED_SAGA)


def test_infrastructure_all_matches_expected() -> None:
    _assert_all_is(pydomain.infrastructure, EXPECTED_INFRASTRUCTURE)


PUBLIC_PACKAGES = [
    pydomain,
    pydomain.ddd,
    pydomain.cqrs,
    pydomain.cqrs.saga,
    pydomain.es,
    pydomain.infrastructure,
    pydomain.testing,
    pydomain.testing.contracts,
]


@pytest.mark.parametrize(
    "module", PUBLIC_PACKAGES, ids=[module.__name__ for module in PUBLIC_PACKAGES]
)
def test_every_exported_name_resolves(module: ModuleType) -> None:
    missing = [name for name in module.__all__ if not hasattr(module, name)]
    assert not missing, f"{module.__name__}.__all__ lists unresolvable {missing}"


# ── README import paths (TEST-07) ───────────────────────────────────────────

_PYTHON_FENCE = re.compile(r"^```python[^\n]*\n(.*?)^```", re.M | re.S)


def _readme_imports() -> list[tuple[str, str | None]]:
    """Every ``import pydomain…`` and ``from pydomain… import name`` in the README."""
    imports: list[tuple[str, str | None]] = []
    for block in _PYTHON_FENCE.findall(README.read_text(encoding="utf-8")):
        for node in ast.walk(ast.parse(block)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "pydomain"
            ):
                imports += [(node.module or "", alias.name) for alias in node.names]
            elif isinstance(node, ast.Import):
                imports += [
                    (alias.name, None)
                    for alias in node.names
                    if alias.name.split(".")[0] == "pydomain"
                ]
    return imports


def test_readme_shows_pydomain_imports() -> None:
    assert _readme_imports(), "README shows no pydomain import; is the parser broken?"


def test_readme_import_paths_resolve() -> None:
    broken = []
    for module_name, name in _readme_imports():
        try:
            module = importlib.import_module(module_name)
        except ImportError as error:
            broken.append(f"import {module_name}: {error}")
            continue
        if name is not None and name != "*" and not hasattr(module, name):
            broken.append(f"from {module_name} import {name}")
    assert not broken, broken
