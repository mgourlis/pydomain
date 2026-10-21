"""Probe: the B-70 depth limit at its boundary, a shared (non-cyclic) container, and a wide payload's cost."""
import time
from typing import Any

from pydantic import ValidationError

from pydomain.cqrs.integration_events import IntegrationEvent


class E(IntegrationEvent):
    data: Any = None


def nest(n):
    v: Any = 1
    for _ in range(n):
        v = [v]
    return v


for n in (99, 100, 101):
    try:
        E(data=nest(n)); print(n, "levels: accepted")
    except ValidationError:
        print(n, "levels: rejected")
shared = {"a": 1}
E(data=[shared, shared, {"x": shared}]); print("shared container: accepted")
wide = [[{"k": [i] * 10} for i in range(1000)] for _ in range(100)]
t = time.perf_counter(); E(data=wide); print(f"1M-leaf payload validated in {time.perf_counter() - t:.2f}s")
deep_wide: Any = list(range(1000))
for _ in range(98):
    deep_wide = [deep_wide] + list(range(1000))
t = time.perf_counter(); E(data=deep_wide); print(f"98-deep x 1000-wide in {time.perf_counter() - t:.2f}s")
