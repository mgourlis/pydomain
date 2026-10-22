"""A17: what static types do the buses return?"""
from uuid import UUID

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, CommandResult
from pydomain.cqrs.queries import Query, QueryResult
from pydomain.cqrs.query_bus import QueryBus
from pydomain.infrastructure.message_bus import MessageBus


class PlaceOrderResult(CommandResult):
    order_id: UUID


class PlaceOrder(Command[PlaceOrderResult]):
    order_id: UUID


class GetOrderResult(QueryResult):
    order_id: UUID


class GetOrder(Query[GetOrderResult]):
    order_id: UUID


async def probe(cb: CommandBus, qb: QueryBus, mb: MessageBus, oid: UUID) -> None:
    reveal_type(await cb.dispatch(PlaceOrder(order_id=oid)))
    reveal_type(await qb.dispatch(GetOrder(order_id=oid)))
    reveal_type(await mb.dispatch(PlaceOrder(order_id=oid)))
    reveal_type(await mb.dispatch(GetOrder(order_id=oid)))
    r, _ = await cb.dispatch(PlaceOrder(order_id=oid))
    r.order_id  # should be an error if result is only CommandResult
    q = await qb.dispatch(GetOrder(order_id=oid))
    q.no_such_field  # Any: silently accepted
