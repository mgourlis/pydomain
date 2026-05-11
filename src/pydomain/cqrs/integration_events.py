from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator
from uuid_utils import uuid7

_ALLOWED_PRIMITIVES: tuple[type, ...] = (
    str,
    int,
    float,
    bool,
    dict,
    list,
    type(None),
)


class IntegrationEvent(BaseModel):
    """Base class for Integration Events.

    Integration Events are the cross-boundary counterpart to Domain Events.
    They carry primitives only (str, int, float, bool, dict, list) and are
    published to external consumers via a MessageBroker.

    Fields are immutable (frozen=True). ``event_id`` and ``occurred_at`` are
    auto-generated as primitive strings to satisfy broker serialization
    requirements.
    """

    event_id: str = Field(default_factory=lambda: str(uuid7()))
    occurred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def _validate_primitive_fields(self) -> IntegrationEvent:
        """Validate that all field values are primitive types.

        Integration events must only carry primitive types to ensure they
        can be serialized by message brokers without custom serialization
        logic.
        """
        for name in type(self).model_fields:
            value = getattr(self, name)
            if not isinstance(value, _ALLOWED_PRIMITIVES):
                raise ValueError(
                    f"Field '{name}' has disallowed type '{type(value).__name__}'. "
                    "IntegrationEvent fields must be primitives only: "
                    "str, int, float, bool, dict, list, None."
                )
        return self
