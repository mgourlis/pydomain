# ADR-055: Declarative Saga Failure via `fail=True` and Callable Reason Parameters

## Status

Accepted

## Date

2026-05-22

## Context

The `on()` DSL ([ADR-028](ADR-028-saga-on-dsl.md)) already supported declarative lifecycle
transitions for `suspend=True` and `complete=True`. However, failure still required an
imperative handler:

```python
# Before ADR-055 — imperative handler required for failure
self.on(
    FraudReviewRejectedDomainEvent,
    handler=self.handle_fraud_rejected
)

async def handle_fraud_rejected(self, event: FraudReviewRejectedDomainEvent) -> None:
    self.dispatch(NotifyCustomerOfCancellationCommand(customer_id=event.customer_id))
    await self.fail(reason=f"Agent {event.agent_id} rejected the order.", compensate=True)
```

This was the only lifecycle transition without a declarative equivalent, forcing developers
to write boilerplate handlers for the common case of "dispatch a command, then fail and
trigger compensations."

Additionally, the reason and description parameters on `on()` (`compensate_description`,
`suspend_reason`) only accepted static strings. When event data was relevant to the message
(e.g., including the agent ID who rejected an order, or the risk score that triggered a
review), developers had to fall back to imperative handlers to construct dynamic messages.

## Decision

We will add a `fail: bool = False` parameter and a `fail_reason: str | Callable[[DomainEvent], str] | None = None`
parameter to `Saga.on()`. When `fail=True`, the mapped handler dispatches the forward
command (via `send`), then calls `await self.fail(reason=f_reason, compensate=True)`.

We will also extend `compensate_description`, `suspend_reason`, and `fail_reason` to accept
`Callable[[DomainEvent], str]` in addition to static strings, enabling dynamic message
construction from event data.

**Mutual exclusion:** `fail=True` is mutually exclusive with both `complete=True` and
`suspend=True`. A step cannot simultaneously fail and complete, nor fail and suspend.
These are enforced at registration time via `SagaConfigurationError`.

**Reason resolution logic:**

```python
# For fail_reason:
f_reason = _fail_reason(evt) if callable(_fail_reason) else (_fail_reason or "Saga failed")

# For suspend_reason:
s_reason = _suspend_reason(evt) if callable(_suspend_reason) else _suspend_reason

# For compensate_description:
desc = _comp_desc(evt) if callable(_comp_desc) else (_comp_desc or "")
```

**Design note on callable vs static fallback:** When `fail_reason` is a callable, its
return value is used as-is — even if it returns an empty string. No `or "Saga failed"`
fallback is applied. This gives callables full control over the reason. The fallback
only applies to static string parameters.

**Example — the motivating use case, now purely declarative:**

```python
self.on(
    FraudReviewRejectedDomainEvent,
    send=lambda e: NotifyCustomerOfCancellationCommand(customer_id=e.customer_id),
    fail=True,
    fail_reason=lambda e: f"Agent {e.agent_id} rejected the order.",
)
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Imperative handler only (status quo) | Asymmetric — `suspend` and `complete` are declarative, but `fail` requires a handler. Forces boilerplate for the most common failure pattern. |
| Separate `fail()` method on the DSL chain (e.g., `self.on(...).fail_if(...)`) | Adds a builder pattern to an otherwise simple registration API. Method chaining complicates type hints and is unidiomatic in this codebase. |
| Event-driven failure — dispatch a `SagaFailedEvent` instead of calling `fail()` directly | Adds indirection without benefit. The saga already owns its lifecycle; routing failure through the event bus would create a circular dependency (saga dispatches event → bus routes to manager → manager calls saga). |
| Only static strings for reasons (no callable support) | Would force continued use of imperative handlers whenever event data is needed in the reason message — defeating the purpose of declarative failure. |

## Consequences

### Positive

- **Symmetry**: All three lifecycle transitions (`suspend`, `complete`, `fail`) are now declarative on `on()`.
- **Zero boilerplate for common failure**: dispatch a notification command, fail with a dynamic reason, trigger compensations — all in one `on()` call.
- **Dynamic reasons**: Callable reasons enable context-rich audit messages (agent ID, risk score, timestamp) without imperative handlers.
- **Backward compatible**: All existing `on()` calls continue to work. The new parameters have defaults that preserve existing behavior.

### Negative

- The `on()` method signature is now denser (two additional parameters: `fail`, `fail_reason`).
- Three new mutual-exclusion validation guards (`fail`+`complete`, `fail`+`suspend`) increase registration-time checks.
- Failure always triggers compensation (`compensate=True`). If a saga needs to fail *without* compensation, an imperative handler is still required.

### Neutral

- The `handler=` parameter path ignores `fail=True` entirely — it's a no-op, not an error. Handler-style users call `self.fail()` directly. This is consistent with how `send`/`compensate`/`suspend`/`complete` are already ignored in handler mode.

## References

- `src/pydomain/cqrs/saga/saga.py` — `on()` method, `_mapped_handler` closure, `fail()` method
- [ADR-028](ADR-028-saga-on-dsl.md) — Saga `on()` DSL for unified command and compensation
- [ADR-033](ADR-033-lifo-compensation-stack.md) — LIFO compensation stack via serialized `CompensationRecord`
- `tests/saga/test_saga_fail_declarative.py` — Basic fail=True tests
- `tests/saga/test_saga_fail_edge_cases.py` — Edge-case and unhappy-path tests for fail=True
- `tests/saga/test_saga_new_features_integration.py` — Integration tests combining fail with other features
