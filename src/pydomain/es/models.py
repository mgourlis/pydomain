from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from pydomain.ddd.domain_event import DomainEvent


class EventStream(BaseModel):
    """A frozen read-only representation of an event stream."""

    events: Sequence[DomainEvent]
    version: int

    model_config = ConfigDict(frozen=True)
