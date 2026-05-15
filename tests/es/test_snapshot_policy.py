"""Tests for SnapshotPolicy protocol and SnapshotThresholdPolicy.

Covers protocol conformance (runtime_checkable isinstance), threshold-based
snapshot decisions, edge cases (zero, negative), instance independence, and
parameter indifference.
"""

from __future__ import annotations

import pytest

from pydomain.es.snapshot import SnapshotPolicy, SnapshotThresholdPolicy

# ===================================================================
# SnapshotPolicy Protocol Conformance
# ===================================================================


class TestSnapshotPolicyProtocol:
    """The ``SnapshotPolicy`` is ``@runtime_checkable`` -- any implementation
    must pass an ``isinstance`` check."""

    def test_threshold_policy_passes_isinstance(self) -> None:
        """``isinstance(SnapshotThresholdPolicy(5), SnapshotPolicy)`` returns
        ``True``."""
        policy = SnapshotThresholdPolicy(5)
        assert isinstance(policy, SnapshotPolicy)

    def test_plain_object_does_not_pass_isinstance(self) -> None:
        """A plain object does NOT pass ``isinstance`` check for
        ``SnapshotPolicy``."""
        assert not isinstance(object(), SnapshotPolicy)


# ===================================================================
# SnapshotThresholdPolicy -- Snapshot decisions
# ===================================================================


class TestSnapshotThresholdPolicy:
    """``SnapshotThresholdPolicy`` decides when to snapshot based on
    ``current_version % threshold == 0`` (or ``pending_event_count > 0`` for
    ``threshold=0``)."""

    def test_threshold_5_returns_true_at_multiples(self) -> None:
        """``threshold=5`` returns True for versions 5, 10, 15."""
        policy = SnapshotThresholdPolicy(threshold=5)
        assert policy.should_snapshot("Order", "order-1", 5, 0) is True
        assert policy.should_snapshot("Order", "order-1", 10, 0) is True
        assert policy.should_snapshot("Order", "order-1", 15, 0) is True

    def test_threshold_5_returns_false_at_non_multiples(self) -> None:
        """``threshold=5`` returns False for versions 4, 9, 11."""
        policy = SnapshotThresholdPolicy(threshold=5)
        assert policy.should_snapshot("Order", "order-1", 4, 0) is False
        assert policy.should_snapshot("Order", "order-1", 9, 0) is False
        assert policy.should_snapshot("Order", "order-1", 11, 0) is False

    def test_threshold_1_returns_true_for_all_versions(self) -> None:
        """``threshold=1`` returns True for all versions (``version % 1 == 0``
        always)."""
        policy = SnapshotThresholdPolicy(threshold=1)
        assert policy.should_snapshot("Order", "order-1", 0, 0) is True
        assert policy.should_snapshot("Order", "order-1", 1, 0) is True
        assert policy.should_snapshot("Order", "order-1", 100, 0) is True

    def test_threshold_0_with_pending_events_returns_true(self) -> None:
        """``threshold=0`` returns True when ``pending_event_count > 0``."""
        policy = SnapshotThresholdPolicy(threshold=0)
        assert policy.should_snapshot("Order", "order-1", 5, 1) is True
        assert policy.should_snapshot("Order", "order-1", 5, 10) is True

    def test_threshold_0_with_no_pending_events_returns_false(self) -> None:
        """``threshold=0`` returns False when ``pending_event_count == 0``."""
        policy = SnapshotThresholdPolicy(threshold=0)
        assert policy.should_snapshot("Order", "order-1", 5, 0) is False

    def test_negative_threshold_raises_value_error(self) -> None:
        """A negative threshold raises ``ValueError`` on init."""
        with pytest.raises(ValueError, match="threshold must be >= 0"):
            SnapshotThresholdPolicy(threshold=-1)

    def test_different_instances_are_independent(self) -> None:
        """Different threshold instances operate independently."""
        policy_a = SnapshotThresholdPolicy(threshold=5)
        policy_b = SnapshotThresholdPolicy(threshold=10)

        # Version 10: both return True (10 % 5 == 0, 10 % 10 == 0)
        assert policy_a.should_snapshot("Order", "order-1", 10, 0) is True
        assert policy_b.should_snapshot("Order", "order-1", 10, 0) is True

        # Version 5: policy_a returns True, policy_b returns False
        assert policy_a.should_snapshot("Order", "order-1", 5, 0) is True
        assert policy_b.should_snapshot("Order", "order-1", 5, 0) is False

    def test_aggregate_type_and_id_are_ignored(self) -> None:
        """``aggregate_type`` and ``aggregate_id`` parameters do not affect
        the result."""
        policy = SnapshotThresholdPolicy(threshold=5)

        result_a = policy.should_snapshot("Order", "order-001", 10, 0)
        result_b = policy.should_snapshot("Cart", "cart-001", 10, 0)
        assert result_a == result_b
