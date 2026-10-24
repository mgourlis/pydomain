"""Value-free descriptions of exceptions for library-built errors and logs.

Every error string, stored error and log line that pydomain builds from an
exception goes through :func:`describe` (ADR-071), so a payload value never
leaves the payload through the library's own text. The handler-failure logs
add :func:`describe_stack`, a stack with types and frames only (ADR-081
point 4). The module imports no other pydomain module, so every layer may
use it.
"""

from __future__ import annotations

import itertools
import re
import traceback

from pydantic import ValidationError

__all__ = ["describe", "describe_stack"]

# pydomain's own notes, matched in full (ADR-081 point 4, B-196): every
# add_note site in pydomain gives one of these, with an exception type name.
_KEPT_NOTE = re.compile(
    r"(clean-up also failed|draining the event buffers also failed): [A-Za-z_]\w*"
)
# The caps of traceback.TracebackException, and a cap on the exceptions.
_MAX_EXCEPTIONS = 100
_MAX_GROUP_WIDTH = 15
_MAX_GROUP_DEPTH = 10
# The frames kept at each end of one exception's stack (B-212).
_KEPT_FRAMES = 25

type _Entry = tuple[BaseException | None, str, int]


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
    Two other strings come from the application and are copied as they are:
    the error type a validator gives a ``PydanticCustomError`` (which can be
    built from the value) and the model's ``title`` when its config sets one.
    """
    name = type(exc).__name__
    if not isinstance(exc, ValidationError):
        return name
    # Only "loc" and "type" are read, so the other parts are not even built;
    # changing these flags changes nothing observable.
    # pragma: no mutate start
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    # pragma: no mutate end
    noun = "error" if len(errors) == 1 else "errors"
    where = ", ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<model>'} ({error['type']})"
        for error in errors
    )
    return f"{name}: {len(errors)} {noun} for {exc.title} at {where}"


def describe_stack(exc: BaseException) -> str:
    """Render *exc*'s stack with exception types and frames only.

    One line per exception, its type name, followed by its frames, oldest
    first, as ``File "<path>", line <n>, in <function>``; then each
    exception reachable from it, once: the members of a group, indented one
    step further and labelled ``Member <i> of <n>:``, then its
    ``__cause__`` (``Caused by:``) and its ``__context__`` unless
    suppressed (``During handling of:``)::

        EventHandlersFailed
          File "/app/bus.py", line 108, in dispatch
          Member 1 of 1: ValueError
            File "/app/handlers.py", line 12, in on_shipped
          Caused by: KeyError
            File "/app/handlers.py", line 9, in on_shipped

    The frames come from the code objects, never from the source, so no
    source line is read. Repeated frames, the frames between the first 25
    and the last 25 of an exception with more than 50, the exceptions past
    the 100th, the members of a group past the 15th and the groups nested
    more than 10 deep are left out, each with a ``… N more`` line, so one
    exception renders at most about 100 lines and the whole stack about
    10,000. No message, local or note is rendered, except pydomain's own
    value-free notes (``clean-up also failed: <Type>`` and ``draining the
    event buffers also failed: <Type>``), as ``Note:`` lines.

    Two strings come from the application and are copied as they are: a
    code object's ``co_filename`` when code is compiled under a name the
    application chose (a template engine, ``exec(compile(src, name,
    ...))``), and the name of an exception class built at run time. No
    payload reaches either inside pydomain.
    """
    lines: list[str] = []
    seen: set[int] = set()
    # (exception, label, indent level), last one first; an entry without
    # an exception is a "… N more" line, its label the whole text.
    pending: list[_Entry] = [(exc, "", 0)]
    shown = 0
    while pending:
        current, label, level = pending.pop()
        pad = "  " * level
        if current is None:
            lines.append(pad + label)
            continue
        if id(current) in seen:
            continue
        if shown == _MAX_EXCEPTIONS:
            left = 1 + sum(
                1 for item, _, _ in pending if item is not None and id(item) not in seen
            )
            lines.append(f"{pad}… {left} more exception(s)")
            break
        seen.add(id(current))
        shown += 1
        lines.append(f"{pad}{label}{type(current).__name__}")
        lines.extend(_frames(current, pad + "  "))
        lines.extend(f"{pad}  Note: {note}" for note in _kept_notes(current))
        context = current.__context__
        if context is not None and not current.__suppress_context__:
            pending.append((context, "During handling of: ", level))
        if current.__cause__ is not None:
            pending.append((current.__cause__, "Caused by: ", level))
        if isinstance(current, BaseExceptionGroup):
            pending.extend(_members(current, level + 1))
    return "\n".join(lines)


def _frames(exc: BaseException, pad: str) -> list[str]:
    """*exc*'s frames, oldest first: a repeated frame collapsed, and past
    50 frames only the first and the last 25 kept (B-212)."""
    frames = (
        (frame.f_code.co_filename, line, frame.f_code.co_name)
        for frame, line in traceback.walk_tb(exc.__traceback__)
    )
    # Each distinct frame with how many times it repeats in a row; a run
    # without a frame stands for the frames left out.
    runs: list[tuple[tuple[str, int | None, str] | None, int]] = [
        (where, sum(1 for _ in same)) for where, same in itertools.groupby(frames)
    ]
    if len(runs) > 2 * _KEPT_FRAMES:
        left = sum(count for _, count in runs[_KEPT_FRAMES:-_KEPT_FRAMES])
        runs = [*runs[:_KEPT_FRAMES], (None, left), *runs[-_KEPT_FRAMES:]]
    lines: list[str] = []
    for where, count in runs:
        if where is None:
            lines.append(f"{pad}… {count} more frame(s)")
            continue
        path, line, function = where
        lines.append(f'{pad}File "{path}", line {line}, in {function}')
        if count > 1:
            lines.append(f"{pad}… {count - 1} more of the frame above")
    return lines


def _kept_notes(exc: BaseException) -> list[str]:
    """The notes of *exc* that are pydomain's own, value-free ones."""
    notes = getattr(exc, "__notes__", None)
    if not isinstance(notes, list | tuple):
        return []
    return [n for n in notes if isinstance(n, str) and _KEPT_NOTE.fullmatch(n)]


def _members(group: BaseExceptionGroup[BaseException], level: int) -> list[_Entry]:
    """The pending entries for *group*'s members, last one first."""
    members = group.exceptions
    count = len(members)
    if level >= _MAX_GROUP_DEPTH:
        return [(None, f"… {count} more member(s)", level)]
    entries: list[_Entry] = []
    if count > _MAX_GROUP_WIDTH:
        entries.append((None, f"… {count - _MAX_GROUP_WIDTH} more member(s)", level))
    entries.extend(
        (members[i], f"Member {i + 1} of {count}: ", level)
        for i in reversed(range(min(count, _MAX_GROUP_WIDTH)))
    )
    return entries
