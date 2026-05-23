"""Tests for the MessagePipeline class.

MessagePipeline is a composable middleware wrapper that runs behaviors in
onion order around a handler. These tests exercise MessagePipeline
directly, independent of the bus implementations.
"""

from __future__ import annotations

from typing import Any

import pytest

from pydomain.cqrs.behaviors import (
    MessageContext,
    MessageKind,
    MessagePipeline,
    NextHandler,
)

# ── Spy behaviors for pipeline tracing ───────────────────────────────────


class SpyBehavior:
    """Pipeline behavior that records execution order to a shared trace list.

    Appends ``{name}_before`` and ``{name}_after`` to ``trace`` around
    the ``next()`` call so tests can verify ordering.
    """

    def __init__(self, name: str, trace: list[str]) -> None:
        self._name = name
        self._trace = trace

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        self._trace.append(f"{self._name}_before")
        result = await next()
        self._trace.append(f"{self._name}_after")
        return result


class ShortCircuitBehavior:
    """Pipeline behavior that does NOT call ``next()``.

    Used to verify that skipping ``next()`` prevents the handler from
    executing and that the short-circuit return value propagates up
    through the pipeline.
    """

    def __init__(self, return_value: Any = "short_circuited") -> None:
        self._return_value = return_value

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        return self._return_value


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def ctx() -> MessageContext:
    """A basic MessageContext for pipeline tests."""
    return MessageContext(
        message="test_message",
        handler=lambda: "handler_result",
        kind=MessageKind.COMMAND,
    )


# ════════════════════════════════════════════════════════════════════════
# Construction tests
# ════════════════════════════════════════════════════════════════════════


class TestConstruction:
    """MessagePipeline construction and behavior list storage."""

    async def _noop_handler(self, message: Any, uow: Any = None) -> None:
        return None

    def test_default_constructs_empty_behaviors(self) -> None:
        """MessagePipeline(handler=h) creates a pipeline with an empty behavior list."""
        pipeline = MessagePipeline(handler=self._noop_handler)
        assert pipeline._behaviors == []

    def test_none_behaviors_equivalent_to_empty(self) -> None:
        """MessagePipeline(handler=h, behaviors=None)
        is equivalent to MessagePipeline(handler=h, behaviors=[]).
        """
        pipeline_none = MessagePipeline(handler=self._noop_handler, behaviors=None)
        pipeline_empty = MessagePipeline(handler=self._noop_handler, behaviors=[])
        assert pipeline_none._behaviors == pipeline_empty._behaviors
        assert pipeline_none._behaviors == []

    def test_stores_behaviors_in_order(self) -> None:
        """MessagePipeline(handler=h, behaviors=[b1, b2])
        stores them in the given order.
        """
        b1 = SpyBehavior("first", [])
        b2 = SpyBehavior("second", [])
        pipeline = MessagePipeline(handler=self._noop_handler, behaviors=[b1, b2])
        assert pipeline._behaviors == [b1, b2]


# ════════════════════════════════════════════════════════════════════════
# Execution tests
# ════════════════════════════════════════════════════════════════════════


class TestExecution:
    """MessagePipeline execution with zero, one, or multiple behaviors."""

    @pytest.mark.anyio
    async def test_execute_with_empty_behaviors_calls_handler(
        self,
        ctx: MessageContext,
    ) -> None:
        """When no behaviors are configured, the handler is called directly."""

        async def handler(message: Any, uow: Any = None) -> str:
            return "done"

        pipeline = MessagePipeline(handler=handler)
        result = await pipeline.execute(ctx, "test_message")
        assert result == "done"

    @pytest.mark.anyio
    async def test_execute_with_single_behavior_wraps_handler(
        self,
        ctx: MessageContext,
    ) -> None:
        """A single behavior wraps the handler in onion order."""
        trace: list[str] = []

        async def handler(message: Any, uow: Any = None) -> str:
            trace.append("handler")
            return "done"

        pipeline = MessagePipeline(
            handler=handler, behaviors=[SpyBehavior("spy", trace)]
        )

        result = await pipeline.execute(ctx, "test_message")

        assert result == "done"
        assert trace == ["spy_before", "handler", "spy_after"]

    @pytest.mark.anyio
    async def test_execute_with_multiple_behaviors_onion_order(
        self,
        ctx: MessageContext,
    ) -> None:
        """Multiple behaviors wrap the handler in correct onion order:
        outer_before -> inner_before -> handler -> inner_after -> outer_after.
        """
        trace: list[str] = []

        async def handler(message: Any, uow: Any = None) -> str:
            trace.append("handler")
            return "done"

        pipeline = MessagePipeline(
            handler=handler,
            behaviors=[
                SpyBehavior("outer", trace),
                SpyBehavior("inner", trace),
            ],
        )

        result = await pipeline.execute(ctx, "test_message")

        assert result == "done"
        assert trace == [
            "outer_before",
            "inner_before",
            "handler",
            "inner_after",
            "outer_after",
        ]

    @pytest.mark.anyio
    async def test_execute_returns_handler_result(
        self,
        ctx: MessageContext,
    ) -> None:
        """The return value of the handler is propagated through the pipeline."""

        async def handler(message: Any, uow: Any = None) -> dict:
            return {"key": "value"}

        pipeline = MessagePipeline(handler=handler)
        result = await pipeline.execute(ctx, "test_message")
        assert result == {"key": "value"}

    @pytest.mark.anyio
    async def test_execute_passes_context_to_behaviors(
        self,
        ctx: MessageContext,
    ) -> None:
        """Behaviors receive the same MessageContext instance passed to execute()."""
        received_ctx: list[MessageContext] = []

        class CapturingBehavior:
            async def handle(
                self,
                mctx: MessageContext,
                nxt: NextHandler,
            ) -> Any:
                received_ctx.append(mctx)
                return await nxt()

        async def handler(message: Any, uow: Any = None) -> str:
            return "done"

        pipeline = MessagePipeline(handler=handler, behaviors=[CapturingBehavior()])

        await pipeline.execute(ctx, "test_message")

        assert len(received_ctx) == 1
        assert received_ctx[0] is ctx

    @pytest.mark.anyio
    async def test_handler_receives_message(self, ctx: MessageContext) -> None:
        """The handler receives the message passed to execute()."""

        async def handler(message: Any, uow: Any = None) -> str:
            return str(message)

        pipeline = MessagePipeline(handler=handler)

        result1 = await pipeline.execute(ctx, "first_call")
        result2 = await pipeline.execute(ctx, "second_call")

        assert result1 == "first_call"
        assert result2 == "second_call"


# ════════════════════════════════════════════════════════════════════════
# Short-circuit tests
# ════════════════════════════════════════════════════════════════════════


class TestShortCircuit:
    """Pipeline behavior short-circuit: when a behavior does not call next()."""

    @pytest.mark.anyio
    async def test_behavior_skips_next_prevents_handler(
        self,
        ctx: MessageContext,
    ) -> None:
        """A behavior that does not call next() prevents the handler from executing."""
        handler_called: bool = False

        async def handler(message: Any, uow: Any = None) -> str:
            nonlocal handler_called
            handler_called = True
            return "from_handler"

        pipeline = MessagePipeline(handler=handler, behaviors=[ShortCircuitBehavior()])

        result = await pipeline.execute(ctx, "test_message")

        assert result == "short_circuited"
        assert not handler_called

    @pytest.mark.anyio
    async def test_short_circuit_result_propagates(
        self,
        ctx: MessageContext,
    ) -> None:
        """A short-circuit return value propagates up through outer behaviors."""
        trace: list[str] = []

        async def handler(message: Any, uow: Any = None) -> str:
            trace.append("handler")
            return "done"

        pipeline = MessagePipeline(
            handler=handler,
            behaviors=[
                SpyBehavior("outer", trace),
                ShortCircuitBehavior(return_value="shorty"),
            ],
        )

        result = await pipeline.execute(ctx, "test_message")

        assert result == "shorty"
        # outer_before runs, then next() calls ShortCircuitBehavior which
        # returns immediately without calling further. The outer spy's after
        # code still executes because await next() returned normally.
        assert trace == ["outer_before", "outer_after"]


# ════════════════════════════════════════════════════════════════════════
# Error propagation tests
# ════════════════════════════════════════════════════════════════════════


class TestErrorPropagation:
    """Pipeline error handling: exceptions in behaviors or the handler."""

    @pytest.mark.anyio
    async def test_exception_in_behavior_propagates(
        self,
        ctx: MessageContext,
    ) -> None:
        """An exception raised inside a behavior propagates through execute()."""

        class FailingBehavior:
            async def handle(
                self,
                mctx: MessageContext,
                nxt: NextHandler,
            ) -> Any:
                msg = "behavior failed"
                raise RuntimeError(msg)

        pipeline = MessagePipeline(
            handler=lambda msg: "done",
            behaviors=[FailingBehavior()],
        )

        with pytest.raises(RuntimeError, match="behavior failed"):
            await pipeline.execute(ctx, "test_message")

    @pytest.mark.anyio
    async def test_exception_in_handler_propagates(
        self,
        ctx: MessageContext,
    ) -> None:
        """An exception raised inside the handler propagates through all
        wrapping behaviors.
        """
        trace: list[str] = []

        class AfterCaptureBehavior:
            async def handle(
                self,
                mctx: MessageContext,
                nxt: NextHandler,
            ) -> Any:
                trace.append("before")
                try:
                    return await nxt()
                finally:
                    trace.append("after")

        async def handler(message: Any, uow: Any = None) -> str:
            msg = "handler failed"
            raise ValueError(msg)

        pipeline = MessagePipeline(handler=handler, behaviors=[AfterCaptureBehavior()])

        with pytest.raises(ValueError, match="handler failed"):
            await pipeline.execute(ctx, "test_message")

        # The behavior's post-next code still runs via the finally block
        assert trace == ["before", "after"]


# ════════════════════════════════════════════════════════════════════════
# Reuse tests
# ════════════════════════════════════════════════════════════════════════


class TestReuse:
    """Pipeline instances are reusable across multiple execute() calls."""

    @pytest.mark.anyio
    async def test_same_pipeline_can_execute_twice(self) -> None:
        """One MessagePipeline instance can execute() multiple times with
        different messages, each producing the correct result.
        """

        async def handler(message: str, uow: Any = None) -> str:
            return message

        pipeline = MessagePipeline(handler=handler)
        ctx1 = MessageContext(
            message="first",
            handler=lambda: None,
            kind=MessageKind.COMMAND,
        )
        ctx2 = MessageContext(
            message="second",
            handler=lambda: None,
            kind=MessageKind.COMMAND,
        )

        result1 = await pipeline.execute(ctx1, "result_a")
        result2 = await pipeline.execute(ctx2, "result_b")

        assert result1 == "result_a"
        assert result2 == "result_b"

    @pytest.mark.anyio
    async def test_reused_pipeline_with_behaviors_produces_correct_order(
        self,
    ) -> None:
        """Reusing the same MessagePipeline instance with behaviors produces
        correct ordering on each independent execution.
        """
        call_count: int = 0

        class CountingBehavior:
            async def handle(
                self,
                mctx: MessageContext,
                nxt: NextHandler,
            ) -> Any:
                nonlocal call_count
                call_count += 1
                return await nxt()

        async def handler(message: str, uow: Any = None) -> str:
            return message

        pipeline = MessagePipeline(
            handler=handler,
            behaviors=[CountingBehavior()],
        )
        ctx = MessageContext(
            message="test",
            handler=lambda: None,
            kind=MessageKind.COMMAND,
        )

        result1 = await pipeline.execute(ctx, "result_a")
        result2 = await pipeline.execute(ctx, "result_b")

        assert result1 == "result_a"
        assert result2 == "result_b"
        assert call_count == 2


# ════════════════════════════════════════════════════════════════════════
# Context mutation tests
# ════════════════════════════════════════════════════════════════════════


class TestContextMutation:
    """Behaviors can share data through MessageContext.metadata."""

    @pytest.mark.anyio
    async def test_behavior_can_mutate_metadata(self) -> None:
        """A behavior can write to ctx.metadata and a downstream behavior
        can read the written value.
        """

        class WriterBehavior:
            async def handle(
                self,
                mctx: MessageContext,
                nxt: NextHandler,
            ) -> Any:
                mctx.metadata["written_by"] = "writer"
                return await nxt()

        class ReaderBehavior:
            async def handle(
                self,
                mctx: MessageContext,
                nxt: NextHandler,
            ) -> Any:
                assert mctx.metadata.get("written_by") == "writer"
                return await nxt()

        async def handler(message: Any, uow: Any = None) -> str:
            return "done"

        pipeline = MessagePipeline(
            handler=handler,
            behaviors=[WriterBehavior(), ReaderBehavior()],
        )
        ctx = MessageContext(
            message="test",
            handler=lambda: None,
            kind=MessageKind.COMMAND,
        )

        result = await pipeline.execute(ctx, "test_message")
        assert result == "done"

    @pytest.mark.anyio
    async def test_context_mutation_visible_to_handler(self) -> None:
        """Mutations made by behaviors to ctx.metadata are visible to the
        handler (via closure).
        """

        class WriterBehavior:
            async def handle(
                self,
                mctx: MessageContext,
                nxt: NextHandler,
            ) -> Any:
                mctx.metadata["key"] = "value_from_behavior"
                return await nxt()

        ctx = MessageContext(
            message="test",
            handler=lambda: None,
            kind=MessageKind.COMMAND,
        )

        async def handler(message: Any, uow: Any = None) -> str:
            # The handler sees metadata written by the behavior
            assert ctx.metadata.get("key") == "value_from_behavior"
            return "done"

        pipeline = MessagePipeline(handler=handler, behaviors=[WriterBehavior()])

        result = await pipeline.execute(ctx, "test_message")
        assert result == "done"
