"""Tests for SagaPruningPolicy protocol and StepThresholdPruningPolicy.

Covers protocol conformance (runtime_checkable isinstance), threshold-based
pruning decisions, safety guards (never prune compensating sagas), edge cases,
instance independence, and parameter behaviour.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

import pytest

from pydomain.cqrs.saga.pruning import SagaPruningPolicy, StepThresholdPruningPolicy
from pydomain.cqrs.saga.state import SagaState, SagaStatus

# ═══════════════════════════════════════════════════════════════════════
# SagaPruningPolicy Protocol Conformance
# ═══════════════════════════════════════════════════════════════════════


class TestSagaPruningPolicyProtocol:
    """The ``SagaPruningPolicy`` is ``@runtime_checkable`` — any
    implementation must pass an ``isinstance`` check."""

    def test_step_threshold_policy_passes_isinstance(self) -> None:
        """``isinstance(StepThresholdPruningPolicy(50), SagaPruningPolicy)``
        returns ``True``."""
        policy = StepThresholdPruningPolicy(step_threshold=50)
        assert isinstance(policy, SagaPruningPolicy)

    def test_plain_object_does_not_pass_isinstance(self) -> None:
        """A plain object does NOT pass ``isinstance`` check for
        ``SagaPruningPolicy``."""
        assert not isinstance(object(), SagaPruningPolicy)

    def test_custom_class_with_should_prune_passes(self) -> None:
        """A class that implements the full protocol matches."""

        class AlwaysPrune:
            @property
            def keep_last_n_steps(self) -> int:
                return 5

            @property
            def keep_last_n_events(self) -> int | None:
                return None

            def should_prune(self, saga_type: str, state: SagaState) -> bool:
                return True

        assert isinstance(AlwaysPrune(), SagaPruningPolicy)

    def test_custom_class_without_should_prune_fails(self) -> None:
        """A class without ``should_prune`` does NOT match the protocol."""

        class NotAPolicy:
            pass

        assert not isinstance(NotAPolicy(), SagaPruningPolicy)


# ═══════════════════════════════════════════════════════════════════════
# StepThresholdPruningPolicy — Step Threshold
# ═══════════════════════════════════════════════════════════════════════


class TestStepThresholdPruningPolicy:
    """``StepThresholdPruningPolicy`` decides when to prune based on
    ``len(state.step_history) >= step_threshold``."""

    def test_returns_false_below_threshold(self) -> None:
        """With 3 steps and threshold=5, should_prune returns False."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(3):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is False

    def test_returns_true_at_threshold(self) -> None:
        """With 5 steps and threshold=5, should_prune returns True."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(5):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is True

    def test_returns_true_above_threshold(self) -> None:
        """With 10 steps and threshold=5, should_prune returns True."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(10):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is True

    def test_returns_false_for_empty_history(self) -> None:
        """With 0 steps and threshold=5, should_prune returns False."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        assert policy.should_prune("TestSaga", state) is False

    def test_default_threshold_is_50(self) -> None:
        """Default threshold of 50 is applied when no threshold is given."""
        policy = StepThresholdPruningPolicy()
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(49):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is False

        state.record_step("step_49", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is True


# ═══════════════════════════════════════════════════════════════════════
# StepThresholdPruningPolicy — Safety Guards
# ═══════════════════════════════════════════════════════════════════════


class TestStepThresholdPruningSafetyGuards:
    """The policy must never recommend pruning for sagas in critical states
    where pruning would compromise correctness."""

    def test_never_prune_compensating_saga(self) -> None:
        """A COMPENSATING saga must not be pruned — compensation stack
        integrity depends on step history."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.COMPENSATING,
        )
        for i in range(10):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is False

    def test_never_prune_suspended_saga(self) -> None:
        """A SUSPENDED saga must not be pruned — it may need full history
        on resume."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.SUSPENDED,
        )
        for i in range(10):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is False

    def test_never_prune_terminal_completed(self) -> None:
        """Terminal COMPLETED sagas should not be pruned (no benefit)."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.COMPLETED,
        )
        for i in range(10):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is False

    def test_never_prune_terminal_failed(self) -> None:
        """Terminal FAILED sagas should not be pruned."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.FAILED,
        )
        for i in range(10):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is False

    def test_never_prune_terminal_compensated(self) -> None:
        """Terminal COMPENSATED sagas should not be pruned."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.COMPENSATED,
        )
        for i in range(10):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is False

    def test_allows_pruning_running_saga(self) -> None:
        """RUNNING sagas CAN be pruned when threshold is met."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.RUNNING,
        )
        for i in range(5):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is True

    def test_allows_pruning_pending_saga(self) -> None:
        """PENDING sagas CAN be pruned when threshold is met."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.PENDING,
        )
        for i in range(5):
            state.record_step(f"step_{i}", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is True


# ═══════════════════════════════════════════════════════════════════════
# StepThresholdPruningPolicy — Configuration Attributes
# ═══════════════════════════════════════════════════════════════════════


class TestStepThresholdPruningConfig:
    """Constructor parameters are exposed as public read-only attributes."""

    def test_step_threshold_exposed(self) -> None:
        """``step_threshold`` is accessible after construction."""
        policy = StepThresholdPruningPolicy(step_threshold=25)
        assert policy.step_threshold == 25

    def test_keep_last_n_steps_exposed(self) -> None:
        """``keep_last_n_steps`` is accessible after construction."""
        policy = StepThresholdPruningPolicy(step_threshold=50, keep_last_n_steps=10)
        assert policy.keep_last_n_steps == 10

    def test_keep_last_n_events_default_none(self) -> None:
        """``keep_last_n_events`` defaults to ``None`` (don't prune events)."""
        policy = StepThresholdPruningPolicy(step_threshold=50)
        assert policy.keep_last_n_events is None

    def test_keep_last_n_events_exposed(self) -> None:
        """``keep_last_n_events`` is accessible after construction."""
        policy = StepThresholdPruningPolicy(step_threshold=50, keep_last_n_events=100)
        assert policy.keep_last_n_events == 100

    def test_default_keep_last_n_steps_is_10(self) -> None:
        """``keep_last_n_steps`` defaults to 10."""
        policy = StepThresholdPruningPolicy()
        assert policy.keep_last_n_steps == 10


# ═══════════════════════════════════════════════════════════════════════
# StepThresholdPruningPolicy — Validation
# ═══════════════════════════════════════════════════════════════════════


class TestStepThresholdPruningValidation:
    """Constructor parameter validation."""

    def test_negative_step_threshold_raises(self) -> None:
        """Negative ``step_threshold`` raises ``ValueError``."""
        with pytest.raises(ValueError, match="step_threshold must be >= 0"):
            StepThresholdPruningPolicy(step_threshold=-1)

    def test_zero_step_threshold_is_valid(self) -> None:
        """``step_threshold=0`` is valid (prune every time)."""
        policy = StepThresholdPruningPolicy(step_threshold=0)
        assert policy.step_threshold == 0

    def test_negative_keep_last_n_steps_raises(self) -> None:
        """Negative ``keep_last_n_steps`` raises ``ValueError``."""
        with pytest.raises(ValueError, match="keep_last_n_steps must be >= 0"):
            StepThresholdPruningPolicy(step_threshold=50, keep_last_n_steps=-5)

    def test_negative_keep_last_n_events_raises(self) -> None:
        """Negative ``keep_last_n_events`` raises ``ValueError``."""
        with pytest.raises(ValueError, match="keep_last_n_events must be >= 0"):
            StepThresholdPruningPolicy(step_threshold=50, keep_last_n_events=-5)


# ═══════════════════════════════════════════════════════════════════════
# StepThresholdPruningPolicy — Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestStepThresholdPruningEdgeCases:
    """Edge cases and special configurations."""

    def test_threshold_0_returns_true_for_running_with_any_steps(self) -> None:
        """``step_threshold=0`` means prune on every evaluation for RUNNING
        sagas with at least one step."""
        policy = StepThresholdPruningPolicy(step_threshold=0)
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        state.record_step("step_0", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is True

    def test_threshold_0_returns_false_for_running_with_no_steps(self) -> None:
        """``step_threshold=0`` with no steps returns False (nothing to prune)."""
        policy = StepThresholdPruningPolicy(step_threshold=0)
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        assert policy.should_prune("TestSaga", state) is False

    def test_threshold_0_still_respects_safety_guards(self) -> None:
        """``step_threshold=0`` does NOT override safety guards."""
        policy = StepThresholdPruningPolicy(step_threshold=0)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.COMPENSATING,
        )
        state.record_step("step_0", "EventOccurred")
        assert policy.should_prune("TestSaga", state) is False

    def test_different_saga_types_independent(self) -> None:
        """Policy decisions are based on state, not saga_type string."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state_a = SagaState(saga_type="OrderSaga", status=SagaStatus.RUNNING)
        state_b = SagaState(saga_type="PaymentSaga", status=SagaStatus.RUNNING)
        for i in range(5):
            state_a.record_step(f"step_{i}", "EventA")
            state_b.record_step(f"step_{i}", "EventB")
        # Same result regardless of saga_type
        assert policy.should_prune("OrderSaga", state_a) is True
        assert policy.should_prune("PaymentSaga", state_b) is True

    def test_different_instances_are_independent(self) -> None:
        """Different policy instances operate independently."""
        policy_a = StepThresholdPruningPolicy(step_threshold=5)
        policy_b = StepThresholdPruningPolicy(step_threshold=10)

        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(7):
            state.record_step(f"step_{i}", "EventOccurred")

        assert policy_a.should_prune("TestSaga", state) is True
        assert policy_b.should_prune("TestSaga", state) is False

    def test_saga_type_param_is_available_for_custom_policies(self) -> None:
        """The ``saga_type`` parameter is available for custom policies that
        differentiate by type, even though StepThresholdPruningPolicy ignores
        it."""
        policy = StepThresholdPruningPolicy(step_threshold=5)
        state = SagaState(saga_type="TypeA", status=SagaStatus.RUNNING)
        for i in range(5):
            state.record_step(f"step_{i}", "EventOccurred")

        result_a = policy.should_prune("TypeA", state)
        result_b = policy.should_prune("TypeB", state)
        assert result_a == result_b  # Ignored by this implementation


# ═══════════════════════════════════════════════════════════════════════
# StepThresholdPruningPolicy — Integration with prune_history()
# ═══════════════════════════════════════════════════════════════════════


class TestStepThresholdPruningIntegration:
    """Verify the policy works correctly with ``SagaState.prune_history()``."""

    def test_prune_with_policy_params_reduces_step_count(self) -> None:
        """After pruning with policy params, step count is reduced to
        ``keep_last_n_steps``."""
        policy = StepThresholdPruningPolicy(step_threshold=5, keep_last_n_steps=2)
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(10):
            state.record_step(f"step_{i}", "EventOccurred")

        assert policy.should_prune("TestSaga", state) is True
        state.prune_history(keep_last_n_steps=policy.keep_last_n_steps)
        assert len(state.step_history) == 2
        assert state.step_history[0].step_name == "step_8"
        assert state.step_history[1].step_name == "step_9"

    def test_prune_with_keep_last_n_events(self) -> None:
        """Pruning with ``keep_last_n_events`` reduces processed event IDs."""
        policy = StepThresholdPruningPolicy(
            step_threshold=3,
            keep_last_n_steps=1,
            keep_last_n_events=2,
        )
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        event_ids = [uuid4() for _ in range(5)]
        for eid in event_ids:
            state.mark_event_processed(eid)
            state.record_step("step", "Event")
        # Force more steps to exceed threshold without more events
        for i in range(2):
            state.record_step(f"extra_{i}", "Event")

        assert len(state.processed_event_ids) == 5
        assert policy.should_prune("TestSaga", state) is True
        state.prune_history(
            keep_last_n_steps=policy.keep_last_n_steps,
            keep_last_n_events=policy.keep_last_n_events,
        )
        assert len(state.step_history) == 1
        assert len(state.processed_event_ids) == 2

    def test_prune_keeps_most_recent_steps(self) -> None:
        """After pruning, only the most recent steps are retained."""
        policy = StepThresholdPruningPolicy(step_threshold=3, keep_last_n_steps=3)
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(10):
            state.record_step(f"step_{i}", "EventOccurred")

        state.prune_history(keep_last_n_steps=policy.keep_last_n_steps)
        assert len(state.step_history) == 3
        assert [r.step_name for r in state.step_history] == [
            "step_7",
            "step_8",
            "step_9",
        ]

    def test_prune_without_event_pruning(self) -> None:
        """When ``keep_last_n_events=None``, processed events are NOT pruned."""
        policy = StepThresholdPruningPolicy(
            step_threshold=3,
            keep_last_n_steps=2,
            keep_last_n_events=None,
        )
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        event_ids = [uuid4() for _ in range(5)]
        for i, eid in enumerate(event_ids):
            state.mark_event_processed(eid)
            state.record_step(f"step_{i}", "Event")
        for i in range(3):
            state.record_step(f"extra_{i}", "Event")

        original_count = len(state.processed_event_ids)
        state.prune_history(
            keep_last_n_steps=policy.keep_last_n_steps,
            keep_last_n_events=policy.keep_last_n_events,
        )
        assert len(state.processed_event_ids) == original_count
        assert len(state.step_history) == 2


# ═══════════════════════════════════════════════════════════════════════
# SagaState.pruning_policy ClassVar — Auto-Pruning via record_step()
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStatePruningPolicyClassVar:
    """``SagaState.pruning_policy`` is an optional ``ClassVar`` that, when
    set on a subclass, auto-evaluates after every ``record_step()`` call."""

    def test_default_pruning_policy_is_none(self) -> None:
        """By default, ``SagaState.pruning_policy`` is ``None`` (no auto-pruning)."""
        assert SagaState.pruning_policy is None

    def test_subclass_with_policy_has_classvar(self) -> None:
        """A subclass can set ``pruning_policy`` at the class level."""

        class MySagaState(SagaState):
            pruning_policy: ClassVar[SagaPruningPolicy | None] = (
                StepThresholdPruningPolicy(step_threshold=5)
            )

        assert isinstance(MySagaState.pruning_policy, SagaPruningPolicy)

    def test_auto_prune_triggers_after_threshold_steps(self) -> None:
        """When ``pruning_policy`` is set, ``record_step()`` auto-prunes
        once ``step_threshold`` is reached."""

        class MySagaState(SagaState):
            pruning_policy: ClassVar[SagaPruningPolicy | None] = (
                StepThresholdPruningPolicy(step_threshold=5, keep_last_n_steps=2)
            )

        state = MySagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(6):
            state.record_step(f"step_{i}", "Event")

        # Pruning fires at step_4 (len=5 >= threshold), prunes to 2.
        # Then step_5 is added: [step_3, step_4, step_5] → len=3 < 5, no prune.
        assert len(state.step_history) == 3
        assert state.step_history[0].step_name == "step_3"
        assert state.step_history[-1].step_name == "step_5"

    def test_auto_prune_respects_safety_guards(self) -> None:
        """Auto-pruning does NOT fire for COMPENSATING sagas."""

        class MySagaState(SagaState):
            pruning_policy: ClassVar[SagaPruningPolicy | None] = (
                StepThresholdPruningPolicy(step_threshold=3, keep_last_n_steps=1)
            )

        state = MySagaState(saga_type="TestSaga", status=SagaStatus.COMPENSATING)
        for i in range(6):
            state.record_step(f"step_{i}", "Event")

        # Safety guard prevents pruning — all steps retained
        assert len(state.step_history) == 6

    def test_auto_prune_respects_terminal_states(self) -> None:
        """Auto-pruning does NOT fire for FAILED sagas."""

        class MySagaState(SagaState):
            pruning_policy: ClassVar[SagaPruningPolicy | None] = (
                StepThresholdPruningPolicy(step_threshold=3, keep_last_n_steps=1)
            )

        state = MySagaState(saga_type="TestSaga", status=SagaStatus.FAILED)
        for i in range(5):
            state.record_step(f"step_{i}", "Event")

        assert len(state.step_history) == 5

    def test_no_auto_prune_when_policy_is_none(self) -> None:
        """When ``pruning_policy`` is ``None``, no auto-pruning occurs."""
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(100):
            state.record_step(f"step_{i}", "Event")

        assert len(state.step_history) == 100

    def test_auto_prune_with_event_pruning(self) -> None:
        """Auto-pruning also prunes processed events when ``keep_last_n_events``
        is configured."""

        class MySagaState(SagaState):
            pruning_policy: ClassVar[SagaPruningPolicy | None] = (
                StepThresholdPruningPolicy(
                    step_threshold=3,
                    keep_last_n_steps=1,
                    keep_last_n_events=2,
                )
            )

        state = MySagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        event_ids = [uuid4() for _ in range(5)]
        for i, eid in enumerate(event_ids):
            state.mark_event_processed(eid)
            state.record_step(f"step_{i}", "Event")
        # Extra steps to cross threshold
        state.record_step("extra_0", "Event")
        state.record_step("extra_1", "Event")

        assert len(state.processed_event_ids) == 2
        assert len(state.step_history) == 1

    def test_subclass_without_policy_no_interference(self) -> None:
        """A subclass that does NOT set ``pruning_policy`` inherits ``None``
        and is unaffected."""

        class PlainSagaState(SagaState):
            pass

        state = PlainSagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        for i in range(50):
            state.record_step(f"step_{i}", "Event")

        assert len(state.step_history) == 50

    def test_auto_prune_incremental_growth(self) -> None:
        """After auto-pruning, the saga continues accumulating new steps and
        can be pruned again when the threshold is hit."""

        class MySagaState(SagaState):
            pruning_policy: ClassVar[SagaPruningPolicy | None] = (
                StepThresholdPruningPolicy(step_threshold=4, keep_last_n_steps=2)
            )

        state = MySagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)

        # First pruning cycle: 0→4 steps, prune to 2
        for i in range(4):
            state.record_step(f"step_{i}", "Event")
        assert len(state.step_history) == 2

        # Continue growing: 2→4 steps again, prune to 2
        state.record_step("step_4", "Event")
        state.record_step("step_5", "Event")
        assert len(state.step_history) == 2
        assert state.step_history[-1].step_name == "step_5"

    def test_auto_prune_suspended_state_safe(self) -> None:
        """Auto-pruning does NOT fire for SUSPENDED sagas."""

        class MySagaState(SagaState):
            pruning_policy: ClassVar[SagaPruningPolicy | None] = (
                StepThresholdPruningPolicy(step_threshold=3, keep_last_n_steps=1)
            )

        state = MySagaState(saga_type="TestSaga", status=SagaStatus.SUSPENDED)
        for i in range(5):
            state.record_step(f"step_{i}", "Event")

        assert len(state.step_history) == 5
