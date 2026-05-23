"""Saga-specific exceptions."""

from __future__ import annotations

from pydomain.cqrs.exceptions import CQRSError


class SagaError(CQRSError):
    """Base class for all saga-related errors."""


class SagaStateError(SagaError):
    """Invalid saga state transition or lifecycle violation."""


class SagaConfigurationError(SagaError):
    """Invalid saga setup — e.g. conflicting handler/send registration."""


class SagaHandlerNotFoundError(SagaError):
    """No handler registered for an event type."""
