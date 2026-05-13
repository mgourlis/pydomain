from pydomain.ddd.exceptions import DomainError


class StreamNotFoundError(DomainError):
    """Raised when an event stream does not exist."""

    def __init__(self, aggregate_id: str) -> None:
        self.aggregate_id = aggregate_id
        super().__init__(f"Event stream for aggregate {aggregate_id!r} not found.")


class StreamAlreadyExistsError(DomainError):
    """Raised when trying to create a stream that already exists."""

    def __init__(self, aggregate_id: str) -> None:
        self.aggregate_id = aggregate_id
        super().__init__(f"Event stream for aggregate {aggregate_id!r} already exists.")
