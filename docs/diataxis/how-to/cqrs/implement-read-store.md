# How to Implement a Read Store

> **Prerequisites:** [Read Models concept](../../concepts/cqrs/read-models.md), [Define a Read Store Protocol](define-read-store-protocol.md)

## Problem

You need to implement the concrete storage and query logic for a read store protocol — querying a database, returning projected data as dicts.

## Solution

Implement the read store protocol with your database of choice. Keep SQL (or equivalent) in the implementation, not the protocol.

## Steps

### 1. Implement the protocol

```python
from uuid import UUID
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession


class PostgresOrderReadStore:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get(self, order_id: UUID) -> dict | None:
        async with self._session_factory() as session:
            row = await session.execute(
                select(OrderProjection).where(
                    OrderProjection.id == order_id
                )
            )
            projection = row.scalar_one_or_none()
            if projection is None:
                return None
            return {
                "order_id": str(projection.id),
                "customer_name": projection.customer_name,
                "total": projection.total_cents,
                "status": projection.status,
                "items": [
                    {
                        "product_id": str(item.product_id),
                        "name": item.product_name,
                        "quantity": item.quantity,
                        "price_cents": item.price_cents,
                    }
                    for item in projection.items
                ],
                "placed_at": projection.placed_at.isoformat(),
            }
```

### 2. Implement paginated queries

```python
async def find_by_customer(
    self,
    customer_id: UUID,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    async with self._session_factory() as session:
        base = select(OrderProjection).where(
            OrderProjection.customer_id == customer_id
        )
        if status is not None:
            base = base.where(OrderProjection.status == status)

        total = await session.scalar(
            select(func.count()).select_from(base.subquery())
        )

        rows = await session.execute(
            base.order_by(OrderProjection.placed_at.desc())
            .offset(offset)
            .limit(limit)
        )

        orders = [
            {
                "order_id": str(row.id),
                "total": row.total_cents,
                "status": row.status,
                "placed_at": row.placed_at.isoformat(),
            }
            for row in rows.scalars()
        ]

        return orders, total
```

### 3. Return dicts

Always return dicts (or simple dataclass/Pydantic model instances) from read store methods. This keeps the protocol implementation-agnostic and the handler decoupled from ORM objects:

```python
# Do: return plain dicts
async def get(self, order_id: UUID) -> dict | None:
    ...
    return {"order_id": ..., "status": ..., "total": ...}

# Don't: return ORM objects
async def get(self, order_id: UUID) -> OrderProjection | None:
    ...  # Leaks ORM into the handler
```

## In-Memory Implementation for Tests

```python
class InMemoryOrderReadStore:
    def __init__(self) -> None:
        self._orders: dict[UUID, dict] = {}

    async def get(self, order_id: UUID) -> dict | None:
        return self._orders.get(order_id)

    async def find_by_customer(
        self,
        customer_id: UUID,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        matching = [
            o for o in self._orders.values()
            if o["customer_id"] == customer_id
            and (status is None or o["status"] == status)
        ]
        total = len(matching)
        page = matching[offset:offset + limit]
        return page, total

    def seed(self, orders: list[dict]) -> None:
        for order in orders:
            self._orders[order["order_id"]] = order
```

## Keeping Projections Updated

Read stores must be kept in sync with the write side. Two approaches:

**Synchronous** (same transaction): Update the read store in an event handler that runs within the Unit of Work:

```python
class UpdateOrderProjectionHandler:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __call__(self, event: OrderPlaced) -> None:
        projection = OrderProjection(
            id=event.order_id,
            customer_id=event.customer_id,
            total_cents=event.total_amount,
            status="placed",
            placed_at=event.occurred_at,
        )
        self._session.add(projection)
```

**Asynchronous** (eventual consistency): Update via an event subscription after the transaction commits. See [Event Sourcing](../../concepts/es/event-sourcing.md) for subscription-based projections.

## See Also

- [Define a Read Store Protocol](define-read-store-protocol.md)
- [Read Models concept](../../concepts/cqrs/read-models.md)
- [Implement a Query Handler](implement-query-handler.md)
- [Build Projections (Recipe)](../recipes/build-projections.md)
