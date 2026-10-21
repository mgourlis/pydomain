from __future__ import annotations

import pytest

from tests.support.leaked_tasks import (  # noqa: F401 - fixtures used by every test
    ALLOW_LEAKED_TASKS,
    check_leaked_tasks,
    leaked_task_check,
)

pytest_plugins = ["pytester"]

# The audit verification probes are scripts, not tests (see support/probes/README.md).
collect_ignore_glob = ["support/probes/*"]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{ALLOW_LEAKED_TASKS}(reason): skip the leaked-task check for this test",
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
