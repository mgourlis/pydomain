"""Hypothesis profiles: ``ci`` is reproducible, ``dev`` is the default.

``tests/conftest.py`` registers both and loads the one named by the
``HYPOTHESIS_PROFILE`` environment variable (``dev`` when unset)::

    HYPOTHESIS_PROFILE=ci python -m pytest
"""

from __future__ import annotations

import os
from collections.abc import Mapping

PROFILE_ENV_VAR = "HYPOTHESIS_PROFILE"
DEFAULT_PROFILE = "dev"


def register_profiles() -> None:
    """Register the ``ci`` and ``dev`` profiles."""
    raise NotImplementedError


def load_profile(environ: Mapping[str, str] = os.environ) -> str:
    """Load the profile named in *environ* (default ``dev``) and return its name."""
    raise NotImplementedError
