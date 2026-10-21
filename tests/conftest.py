from __future__ import annotations

import pytest

# The audit verification probes are scripts, not tests (see support/probes/README.md).
collect_ignore_glob = ["support/probes/*"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
