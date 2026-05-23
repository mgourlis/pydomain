# How to Configure the Query Bus

> **Prerequisites:** [Query Bus concept](../../concepts/cqrs/query-bus.md)

## Problem

You need to wire up the Query Bus — register handlers for each query type — so that query dispatch works end-to-end.

## Solution

Create a `QueryBus` instance. Register each query type with its handler and optional pipeline behaviors.

## Steps

### 1. Create the bus

```python
from pydomain.cqrs.query_bus import QueryBus

bus = QueryBus()
```

### 2. Register handlers

```python
bus.register(
    query_type=GetOrder,
    handler=GetOrderHandler(order_read_store),
)

bus.register(
    query_type=FindOrders,
    handler=FindOrdersHandler(order_read_store),
)
```

No UoW factory — queries are read-only.

### 3. Dispatch queries

```python
result = await bus.dispatch(GetOrder(order_id=some_id))
# result: GetOrderResult — typed, no events tuple
```

## Adding Pipeline Behaviors

Pass behaviors at registration time:

```python
from pydomain.cqrs.behaviors import LoggingBehavior, ValidationBehavior

bus.register(
    query_type=GetOrder,
    handler=GetOrderHandler(order_read_store),
    behaviors=[
        LoggingBehavior(),
        ValidationBehavior(validators={
            GetOrder: [validate_order_id_not_empty],
        }),
    ],
)
```

Common query behaviors: logging and validation. Idempotency and locking are typically command-only.

## Multiple Read Stores

Different queries may use different read stores:

```python
# Order queries
bus.register(GetOrder, GetOrderHandler(order_read_store))
bus.register(FindOrders, FindOrdersHandler(order_read_store))

# Customer queries
bus.register(GetCustomer, GetCustomerHandler(customer_read_store))
bus.register(FindCustomers, FindCustomersHandler(customer_read_store))
```

Each handler receives the read store it needs via constructor injection.

## Bootstrap Function

Extract wiring into a bootstrap function:

```python
def bootstrap_query_bus(
    order_store: OrderReadStore,
    customer_store: CustomerReadStore,
) -> QueryBus:
    bus = QueryBus()
    behaviors = [LoggingBehavior()]

    bus.register(GetOrder, GetOrderHandler(order_store), behaviors)
    bus.register(FindOrders, FindOrdersHandler(order_store), behaviors)
    bus.register(GetCustomer, GetCustomerHandler(customer_store), behaviors)

    return bus
```

## Verification

Test with a [Fake Read Store](#):

```python
class FakeOrderReadStore:
    def __init__(self, orders: dict[UUID, dict] | None = None) -> None:
        self._orders = orders or {}

    async def get(self, order_id: UUID) -> dict | None:
        return self._orders.get(order_id)


async def test_get_order_query():
    store = FakeOrderReadStore({
        order_id: {"order_id": order_id, "status": "placed", "total": 1000}
    })
    bus = QueryBus()
    bus.register(GetOrder, GetOrderHandler(store))

    result = await bus.dispatch(GetOrder(order_id=order_id))

    assert result.status == "placed"
    assert result.total == 1000
```

## See Also

- [Query Bus concept](../../concepts/cqrs/query-bus.md)
- [Configure the Command Bus](configure-command-bus.md)
- [Implement a Query Handler](implement-query-handler.md)
- [Bootstrap the Application](../infrastructure/bootstrap-application.md)
