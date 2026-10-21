"""Value-free descriptions of exceptions for library-built errors and logs.

Every error string, stored error and log line that pydomain builds from an
exception goes through :func:`describe` (ADR-071), so a payload value never
leaves the payload through the library's own text. The module imports no
other pydomain module, so every layer may use it.
"""

from __future__ import annotations

from pydantic import ValidationError

__all__ = ["describe"]


def describe(exc: BaseException) -> str:
    """Describe *exc* by its type and, for a ``ValidationError``, its locations.

    A Pydantic ``ValidationError`` gives one line with the model, the error
    count and each failing location with its error code::

        ValidationError: 2 errors for Invoice at amount (int_parsing), id (missing)

    Any other exception gives its type name only. Neither ever includes an
    input value, a Pydantic ``msg`` or ``ctx``, or ``str(exc)``, which may
    quote the payload. Locations are copied as Pydantic reports them, so the
    keys of a ``dict`` field and unknown keys rejected by ``extra="forbid"``
    do appear; an error on the whole model has the location ``<model>``.
    """
    name = type(exc).__name__
    if not isinstance(exc, ValidationError):
        return name
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    noun = "error" if len(errors) == 1 else "errors"
    where = ", ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<model>'} ({error['type']})"
        for error in errors
    )
    return f"{name}: {len(errors)} {noun} for {exc.title} at {where}"
