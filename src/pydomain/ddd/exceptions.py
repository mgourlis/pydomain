from typing import Any


class DomainError(Exception):
    """Base class for all domain-layer errors."""


class ConcurrencyError(DomainError):
    """Optimistic concurrency conflict — the aggregate version changed."""


class SpecificationError(DomainError):
    """A specification-based validation rule failed."""


def _restore_exception(
    cls: type[BaseException], args: tuple[Any, ...], state: dict[str, Any]
) -> BaseException:
    """Rebuild a pickled or copied exception without calling ``__init__``.

    An exception whose ``__init__`` takes other arguments than its message
    returns ``(_restore_exception, (type(self), self.args, self.__dict__))``
    from ``__reduce__`` (B-208, B-210). ``BaseException.__reduce__`` would
    pass ``self.args`` back to ``__init__``, which doubles the message or
    raises ``TypeError``; passing the constructor arguments back instead
    rebuilds a subclass with another signature wrongly. ``__cause__``,
    ``__context__`` and the traceback are not kept, as for any exception,
    and a subclass that adds ``__slots__`` loses its slot state.

    A class that is not an exception is refused before any of its code
    runs, so a pickle cannot use this function to build other objects.
    """
    if not (isinstance(cls, type) and issubclass(cls, BaseException)):
        raise TypeError("Only an exception class can be restored")
    exc = cls.__new__(cls)
    exc.args = args
    exc.__dict__.update(state)
    return exc
