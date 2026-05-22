"""Protocol conformance tests for ``MessageSubscriber``.

Follows the same pattern as ``tests/cqrs/test_handlers.py``.
"""

from __future__ import annotations

from typing import Any

from pydomain.infrastructure.message_subscriber import MessageSubscriber


class TestMessageSubscriber:
    def test_is_runtime_checkable(self) -> None:
        """A class with all required methods passes isinstance check."""

        class ValidSubscriber:
            async def subscribe(self, topic: str, handler: Any) -> None: ...

            async def start(self) -> None: ...

            async def stop(self) -> None: ...

        assert isinstance(ValidSubscriber(), MessageSubscriber)

    def test_rejects_non_subscriber(self) -> None:
        """A class without required methods fails isinstance check."""

        class NotSubscriber:
            pass

        assert not isinstance(NotSubscriber(), MessageSubscriber)
