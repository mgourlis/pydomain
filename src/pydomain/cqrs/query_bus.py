"""Query bus for the CQRS layer.

The ``QueryBus`` routes a query to its single registered handler and
returns a typed result. Queries are read-only -- no Unit of Work, no
side effects, no events.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydomain.cqrs.behaviors import (
    MessageContext,
    MessageKind,
    PipelineBehavior,
    _run_pipeline,
)
from pydomain.cqrs.exceptions import (
    HandlerAlreadyRegisteredError,
    NoHandlerRegisteredError,
)
from pydomain.cqrs.queries import Query


class QueryBus:
    """Routes queries to their single handler and returns a typed result.

    Queries are read-only -- there is no Unit of Work on the query path.
    The bus does not collect or stamp domain events.

    Type safety is provided by the ``QueryHandler`` protocol. The bus
    itself uses ``Callable[[Any], Any]`` because handlers are heterogeneous
    and stored in a single dict.
    """

    def __init__(self) -> None:
        self._handlers: dict[
            type[Query[Any]],
            tuple[Callable[[Any], Any], list[PipelineBehavior]],
        ] = {}

    def register(
        self,
        query_type: type[Query[Any]],
        handler: Callable[[Any], Any],
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        """Register a handler for a query type.

        Raises ``HandlerAlreadyRegisteredError`` if a handler is already
        registered for ``query_type``.
        """
        if query_type in self._handlers:
            raise HandlerAlreadyRegisteredError(
                f"Handler already registered for {query_type.__name__}"
            )
        self._handlers[query_type] = (handler, behaviors or [])

    async def dispatch(self, query: Query[Any]) -> Any:
        """Dispatch a query to its handler and return the typed result.

        No Unit of Work context -- queries are read-only by contract.

        Returns the handler's result directly (typed as the query's
        bound TResult).
        """
        entry = self._handlers.get(type(query))
        if entry is None:
            raise NoHandlerRegisteredError(
                f"No handler registered for {type(query).__name__}"
            )

        handler, behaviors = entry

        async def terminal() -> Any:
            return await handler(query)

        ctx = MessageContext(
            message=query,
            handler=handler,
            kind=MessageKind.QUERY,
            uow=None,
            correlation_id=query.query_id,
            causation_id=query.query_id,
            metadata={"query_id": str(query.query_id)},
        )

        return await _run_pipeline(behaviors, ctx, terminal)
