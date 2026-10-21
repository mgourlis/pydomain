"""A suite's adapter fixture may be async and may clean up after itself."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from pydomain.testing import FakeCheckpointStore
from pydomain.testing.contracts import CheckpointStoreContract

pytest_plugins = ["pytester"]


class TestAsyncGeneratorFixtureContract(CheckpointStoreContract):
    """Every case, including the Protocol check, runs with an async fixture."""

    @pytest.fixture
    async def checkpoint_store(self) -> AsyncIterator[FakeCheckpointStore]:
        yield FakeCheckpointStore()


_RECORDING_SUITE = """
from pathlib import Path

import pytest

from pydomain.testing import FakeCheckpointStore
from pydomain.testing.contracts import CheckpointStoreContract

LOG = Path(__file__).with_name("teardowns.log")


class TestRecordingTeardown(CheckpointStoreContract):
    @pytest.fixture
    async def checkpoint_store(self):
        yield FakeCheckpointStore()
        with LOG.open("a") as log:
            log.write("torn down\\n")
"""


def test_async_generator_fixture_is_torn_down_after_every_case(
    pytester: pytest.Pytester,
) -> None:
    """The code after ``yield`` runs once per case."""
    pytester.makepyfile(test_suite=_RECORDING_SUITE)

    result = pytester.runpytest_subprocess("-p", "no:cov")

    result.assert_outcomes(passed=5)
    log = pytester.path / "teardowns.log"
    assert log.read_text().splitlines() == ["torn down"] * 5
