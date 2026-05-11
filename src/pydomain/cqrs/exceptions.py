from pydomain.ddd.exceptions import DomainError


class CQRSError(DomainError):
    """Base class for all CQRS-layer errors."""


class HandlerAlreadyRegisteredError(CQRSError):
    """Raised when registering a handler for a message type that already has one."""


class NoHandlerRegisteredError(CQRSError):
    """Raised when dispatching a message with no registered handler."""
