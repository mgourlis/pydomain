# ADR-057: Class-Level Default Timeout with Sentinel and Step Overrides

## Status

Accepted

## Date

2026-05-22

## Context

ADR-034 introduced per-step `suspend_timeout` on the `on()` DSL, allowing each suspension
point to specify its own expiry. However, sagas with many suspension steps had no way to
set a global default — developers had to repeat the same `suspend_timeout=timedelta(days=7)`
on every `on()` call:

```python
# Before ADR-057 — repeated timeout on every suspension step
self.on(Step1Event, send=..., step="step1", suspend=True, suspend_timeout=timedelta(days=7))
self.on(Step2Event, send=..., step="step2", suspend=True, suspend_timeout=timedelta(days=7))
self.on(Step3Event, send=..., step="step3", suspend=True, suspend_timeout=timedelta(days=7))
```

This is repetitive and error-prone — changing the SLA for the saga requires updating every
step. It also provides no way to express "use the default" vs "explicitly infinite" at the
call site, because Python's default parameter value for `suspend_timeout` was `None`, which
also means "infinite." The parameter `suspend_timeout=None` was ambiguous: did the developer
want infinite suspension, or did they just omit the parameter expecting a default?

## Decision

We will add a class-level `default_timeout` attribute and a module-level sentinel to
disambiguate "not provided" from explicit `None`.

**Step 1 — Class-level default:**

```python
class Saga[S: SagaState]:
    # Global default timeout for this saga class.
    # None means no timeout (infinite suspension) by default.
    default_timeout: ClassVar[timedelta | None] = None
```

**Step 2 — Sentinel object:**

```python
# Module-level sentinel to distinguish "omitted" from explicitly "None"
USE_DEFAULT_TIMEOUT = object()
```

**Step 3 — Sentinel-based resolution in `_mapped_handler`:**

```python
# In the suspend branch of _mapped_handler:
if _suspend_timeout is USE_DEFAULT_TIMEOUT:
    resolved_timeout = self.default_timeout
else:
    resolved_timeout = _suspend_timeout  # explicit timedelta or explicit None

self.suspend(reason=s_reason, timeout=resolved_timeout)
```

The sentinel is compared by **identity** (`is`), not truthiness. This is critical: `None`
is falsy, and `timedelta(0)` is also falsy. An identity check against a unique object is
unambiguous.

**Resolution matrix:**

| `default_timeout` | `suspend_timeout` argument | Resolved timeout |
|---|---|---|
| `None` (default) | Omitted / sentinel | `None` (infinite) |
| `None` (default) | `timedelta(hours=24)` | `timedelta(hours=24)` |
| `None` (default) | `None` (explicit) | `None` (infinite) |
| `timedelta(days=7)` | Omitted / sentinel | `timedelta(days=7)` |
| `timedelta(days=7)` | `timedelta(hours=1)` | `timedelta(hours=1)` |
| `timedelta(days=7)` | `None` (explicit) | `None` (infinite override) |

**Example — saga with global SLA, step override, and infinite override:**

```python
class EmployeeOnboardingSaga(Saga[OnboardingState]):
    # Global fallback: 7-day SLA for any step that doesn't specify its own timeout
    default_timeout = timedelta(days=7)

    def __init__(self, state: OnboardingState) -> None:
        super().__init__(state)

        # Uses global 7-day timeout (sentinel → default_timeout)
        self.on(
            LaptopOrdered,
            send=lambda e: ProvisionAccountsCommand(...),
            step="waiting_for_it",
            suspend=True,
        )

        # Overrides with a strict 1-hour timeout
        self.on(
            AccountsProvisioned,
            send=lambda e: NotifySecurityTeamCommand(...),
            step="security_review",
            suspend=True,
            suspend_timeout=timedelta(hours=1),
        )

        # Overrides default to infinite (explicit None passes through)
        self.on(
            SecurityApproved,
            send=lambda e: RequestManagerSignoffCommand(...),
            step="manager_signoff",
            suspend=True,
            suspend_timeout=None,
        )
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `None` as default with no sentinel (status quo) | Ambiguous: `suspend_timeout=None` could mean "use default" or "infinite." When `default_timeout=timedelta(days=7)`, there's no way to explicitly request infinite suspension at a specific step. |
| Configuration dict (e.g., `timeouts={"step1": timedelta(...), "step2": None}`) | Separates timeout configuration from the step definition in `on()`. Loses locality — the timeout is a property of the step, not a separate concern. |
| Per-step only (no class default) | Repetitive for sagas with many suspension points sharing the same SLA. Changing the SLA requires touching every step. |
| `None` as sentinel (overload `None` to mean "use default") | Prevents explicit `None` for infinite suspension — the most natural way to express "never expire." Forces a workaround like `timedelta(days=9999)`. |

## Consequences

### Positive

- **DRY**: Set the SLA once at the class level, override only where needed.
- **Unambiguous**: The sentinel eliminates the "not provided" vs "explicit None" ambiguity.
- **Three-tier flexibility**: omit (use default), explicit `timedelta` (local override), explicit `None` (infinite override).
- **Backward compatible**: `default_timeout` defaults to `None`, matching the pre-ADR-057 behavior where omitted timeout means infinite. Existing sagas are unaffected.
- **Cross-class isolation**: Each saga class has its own `default_timeout`. A `ShortTimeoutSaga` with a 5-minute default does not affect a `LongTimeoutSaga` with a 30-day default.

### Negative

- Developers must understand the sentinel pattern to use explicit `None` for infinite override. The default (omit) already gives infinite when `default_timeout=None`, so this only matters when overriding a non-None default.
- The `suspend_timeout` type annotation is now `timedelta | None | object`, which is less precise than the previous `timedelta | None`. The `object` type is necessary because the sentinel is not a `timedelta` or `None`, but it weakens static type checking at the call site.

### Neutral

- `default_timeout=timedelta(0)` sets `timeout_at` to now, causing immediate expiry on the next `process_timeouts()` cycle. This is a valid (if unusual) configuration for sagas that should never wait.
- The `on_timeout()` default behavior (fail with compensation) is unchanged by this ADR. It operates on `timeout_at` regardless of how the timeout was resolved.

## References

- `src/pydomain/cqrs/saga/saga.py` — `USE_DEFAULT_TIMEOUT` sentinel, `default_timeout` class variable, `on()` suspend_timeout resolution, `_mapped_handler` closure
- [ADR-034](ADR-034-saga-suspension-with-timeout.md) — Saga suspension with timeout (human-in-the-loop)
- `tests/saga/test_saga_timeout_defaults.py` — Basic default_timeout tests
- `tests/saga/test_saga_timeout_edge_cases.py` — Edge-case tests for sentinel, resolution matrix, cross-class isolation
- `tests/saga/test_saga_new_features_integration.py` — Integration tests combining default_timeout with fail and resumes_from
