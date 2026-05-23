"""Command hydration — reconstruct Command instances from serialised data."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def hydrate_command(
    module_name: str,
    command_type: str,
    data: dict[str, Any],
) -> Any | None:
    """Rehydrate a ``Command`` from its module path, type name, and data.

    Uses ``importlib`` to resolve the module and ``model_validate()`` to
    reconstruct the instance.  Returns ``None`` if the module or type
    cannot be resolved (e.g. command from a different service).

    Unknown keys in *data* are stripped before validation so that
    ``extra="forbid"`` on the model does not reject the payload.
    This makes hydration resilient to schema evolution — the write
    side already validated when the command was first created.
    """
    import importlib

    if not module_name or not command_type:
        return None

    try:
        mod = importlib.import_module(module_name)
        cls = getattr(mod, command_type, None)
        if cls is None:
            return None
        known_fields = set(cls.model_fields)
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls.model_validate(filtered)
    except (ImportError, AttributeError) as exc:
        logger.warning("Could not resolve %s.%s: %s", module_name, command_type, exc)
        return None
    except Exception as exc:
        # ValidationError (missing required fields, wrong types), etc.
        # Stripping extras is handled above; any remaining failure means
        # the data genuinely cannot produce a valid instance.
        logger.warning(
            "Validation failed for %s.%s: %s", module_name, command_type, exc
        )
        return None
