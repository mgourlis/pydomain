"""Infrastructure package for DDD/CQRS/ES library.

Provides concrete infrastructure implementations. Interfaces and
abstract base classes live in the CQRS layer as Clean Architecture ports.
"""

from pydomain.infrastructure.message_bus import MessageBus

__all__ = [
    "MessageBus",
]
