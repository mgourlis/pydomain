"""Reusable pytest contract suites, one per pydomain Protocol.

Each suite is a base test class. Subclass it in your own test module, name
the subclass ``Test*`` so pytest collects it, and override the suite's
fixture to return your adapter::

    import pytest

    from pydomain.testing.contracts import CheckpointStoreContract


    class TestPostgresCheckpointStore(CheckpointStoreContract):
        @pytest.fixture
        def checkpoint_store(self) -> PostgresCheckpointStore:
            return PostgresCheckpointStore(...)

Every case asserts only what the Protocol's docstring, or its governing ADR,
promises. The cases are async and run under the ``anyio`` pytest plugin.

This package imports ``pytest`` and ``anyio``. Install them with the
``testing`` extra (``pip install pydomain-lib[testing]``). ``pydomain`` and
``pydomain.testing`` never import this package, so neither becomes a runtime
dependency.
"""

from pydomain.testing.contracts.checkpoint_store import CheckpointStoreContract
from pydomain.testing.contracts.event_store import EventStoreContract
from pydomain.testing.contracts.lock_provider import LockProviderContract
from pydomain.testing.contracts.message_broker import MessageBrokerContract
from pydomain.testing.contracts.message_subscriber import MessageSubscriberContract
from pydomain.testing.contracts.outbox_store import OutboxStoreContract
from pydomain.testing.contracts.processed_message_store import (
    ProcessedMessageStoreContract,
)
from pydomain.testing.contracts.projection_store import ProjectionStoreContract
from pydomain.testing.contracts.repository import RepositoryContract
from pydomain.testing.contracts.saga_repository import SagaRepositoryContract
from pydomain.testing.contracts.snapshot_store import SnapshotStoreContract
from pydomain.testing.contracts.unit_of_work import UnitOfWorkContract

__all__ = [
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
