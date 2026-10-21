"""The suites run on asyncio unless a subclass overrides ``anyio_backend``."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

_CONFTEST = """
import pytest


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request):
    return request.param
"""

_SUITE = """
import pytest

from pydomain.testing import FakeCheckpointStore
from pydomain.testing.contracts import CheckpointStoreContract


class TestDefaultBackend(CheckpointStoreContract):
    @pytest.fixture
    def checkpoint_store(self):
        return FakeCheckpointStore()
"""

_OVERRIDE = """

class TestOverriddenBackend(TestDefaultBackend):
    @pytest.fixture(params=["trio"])
    def anyio_backend(self, request):
        return request.param
"""


def _collected(pytester: pytest.Pytester, source: str) -> list[str]:
    pytester.makeconftest(_CONFTEST)
    pytester.makepyfile(test_suite=source)
    result = pytester.runpytest_subprocess("--collect-only", "-q", "-p", "no:cov")
    assert result.ret == pytest.ExitCode.OK, result.outlines
    return [line for line in result.outlines if "::" in line]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-110: the suites do not pin the anyio backend yet",
)
def test_cases_run_on_asyncio_even_if_conftest_adds_backends(
    pytester: pytest.Pytester,
) -> None:
    """A conftest that parametrizes ``anyio_backend`` does not reach the suite."""
    collected = _collected(pytester, _SUITE)

    assert collected
    assert not [node for node in collected if "trio" in node]


def test_subclass_overrides_anyio_backend(pytester: pytest.Pytester) -> None:
    """Overriding ``anyio_backend`` in the subclass chooses the backend."""
    collected = _collected(pytester, _SUITE + _OVERRIDE)

    overridden = [node for node in collected if "TestOverriddenBackend" in node]
    assert overridden
    assert all(node.endswith("[trio]") for node in overridden)
