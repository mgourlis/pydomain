from __future__ import annotations

import pytest

# The audit verification probes are scripts, not tests (see support/probes/README.md).
collect_ignore_glob = ["support/probes/*"]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "allow_leaked_tasks(reason): skip the leaked-task check for this test",
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
