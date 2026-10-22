from pydantic import BaseModel, ConfigDict


class ValueObject(BaseModel):
    """Base class for Value Objects.

    Value Objects are immutable, defined by their attributes rather than identity.
    Two Value Objects with the same field values are considered equal.

    Subclasses should implement domain-specific operations (e.g. ``__add__``,
    ``__mul__``) that return new instances, built by calling the class so
    every validator runs, rather than mutating in place — the
    closure-of-operations pattern. Avoid ``model_copy(update=...)``: it
    skips validation. Frozen blocks field assignment only; use immutable
    field types (``tuple``, ``frozenset``) so the instance stays hashable.
    """

    model_config = ConfigDict(frozen=True)
