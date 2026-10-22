"""The contract suites must not make pytest a runtime dependency."""

from __future__ import annotations

import subprocess
import sys


def test_importing_pydomain_and_testing_loads_no_test_framework() -> None:
    """``import pydomain, pydomain.testing`` loads neither pytest nor the suites."""
    code = (
        "import sys, pydomain, pydomain.testing; "
        "print(sorted(name for name in "
        "('pytest', '_pytest', 'anyio', 'pydomain.testing.contracts') "
        "if name in sys.modules))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]"
