"""FakeOutboxStore behaviour beyond the OutboxStore contract (ADR-074)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pydomain.cqrs.exceptions import CQRSError
from pydomain.cqrs.outbox import OutboxEntry
from pydomain.testing import FakeOutboxStore

_STALE = "DCE-122 FAKE-05: FakeOutboxStore has no claim_timeout"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _entry(message_id: str) -> OutboxEntry:
    return OutboxEntry(
        message_id=message_id,
        topic="invoices.issued",
        payload={"invoice_ref": "synthetic"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestFakeOutboxStoreClaimTimeout:
    @pytest.mark.parametrize("seconds", [0, -1])
    def test_non_positive_claim_timeout_raises_value_error(self, seconds: int) -> None:
        with pytest.raises(ValueError, match="^claim_timeout must be positive$"):
            FakeOutboxStore(claim_timeout=timedelta(seconds=seconds))

    @pytest.mark.anyio
    async def test_default_claim_lasts_five_minutes(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry("m1")])
        await store.fetch_unpublished()

        clock.now += timedelta(minutes=5) - timedelta(microseconds=1)
        assert await store.fetch_unpublished() == []

        clock.now += timedelta(microseconds=1)
        assert [e.message_id for e in await store.fetch_unpublished()] == ["m1"]


class TestFakeOutboxStoreClaims:
    @pytest.mark.anyio
    async def test_claim_is_held_until_the_claim_timeout(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=30))
        await store.append([_entry("m1")])
        await store.fetch_unpublished()

        clock.now += timedelta(seconds=29)

        assert await store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_stale_claim_is_fetched_again(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=30))
        await store.append([_entry("m1")])
        await store.fetch_unpublished()

        clock.now += timedelta(seconds=30)

        assert [e.message_id for e in await store.fetch_unpublished()] == ["m1"]

    @pytest.mark.anyio
    async def test_dead_lettered_entry_stays_hidden_after_its_claim_expires(
        self,
    ) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=30))
        await store.append([_entry("m1")])
        await store.fetch_unpublished()
        await store.mark_dead_lettered("m1", error="gave up")

        clock.now += timedelta(minutes=5)

        assert await store.fetch_unpublished() == []


class TestFakeOutboxStoreDetails:
    """Fake behaviour the contract suite does not pin."""

    @pytest.mark.anyio
    async def test_default_limit_is_100(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry(f"m{n}") for n in range(101)])

        assert len(await store.fetch_unpublished()) == 100

    @pytest.mark.anyio
    async def test_limit_error_message(self) -> None:
        with pytest.raises(ValueError, match="^limit must be an int of at least 1$"):
            await FakeOutboxStore().fetch_unpublished(0)

    @pytest.mark.anyio
    async def test_entry_due_exactly_now_is_fetched(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry("m1")])
        await store.mark_failed("m1", error="synthetic", next_attempt_at=clock.now)

        assert [e.message_id for e in await store.fetch_unpublished()] == ["m1"]

    @pytest.mark.anyio
    async def test_entry_waiting_for_retry_does_not_hide_later_ones(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry("m1"), _entry("m2")])
        await store.mark_failed(
            "m1", error="synthetic", next_attempt_at=clock.now + timedelta(hours=1)
        )

        assert [e.message_id for e in await store.fetch_unpublished()] == ["m2"]

    @pytest.mark.anyio
    async def test_dead_letter_without_earlier_failures_counts_one_attempt(
        self,
    ) -> None:
        store = FakeOutboxStore()
        await store.append([_entry("m1")])

        await store.mark_dead_lettered("m1", error="synthetic")

        assert store.attempts["m1"] == 1

    @pytest.mark.anyio
    async def test_injected_append_failure_fires_once(self) -> None:
        store = FakeOutboxStore()
        store.fail_next_append = True

        with pytest.raises(
            RuntimeError, match="^FakeOutboxStore: injected append failure$"
        ):
            await store.append([_entry("m1")])
        await store.append([_entry("m2")])

        assert [e.message_id for e in store.entries] == ["m2"]

    @pytest.mark.anyio
    async def test_injected_fetch_failure_fires_once(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry("m1")])
        store.fail_next_fetch = True

        with pytest.raises(
            RuntimeError, match="^FakeOutboxStore: injected fetch failure$"
        ):
            await store.fetch_unpublished()

        assert [e.message_id for e in await store.fetch_unpublished()] == ["m1"]


_B71 = "DCE-122 B-71: a mark after publication un-publishes the entry in the fake"
_LIMIT = "DCE-122 B-58 (review ⑨-6): the fake accepts a non-int limit"


class TestFakeOutboxStorePublishedWins:
    @pytest.mark.anyio
    async def test_marks_after_publication_are_no_ops(self) -> None:
        clock = _Clock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry("m1")])
        await store.mark_published(["m1"])

        await store.mark_failed("m1", error="late", next_attempt_at=clock.now)
        await store.mark_dead_lettered("m1", error="late")

        assert store.is_published("m1")
        assert not store.is_dead_lettered("m1")
        assert store.failures == []
        assert store.attempts.get("m1", 0) == 0


class TestFakeOutboxStoreLimitType:
    @pytest.mark.anyio
    @pytest.mark.parametrize("limit", [1.5, True])
    async def test_non_int_limit_raises_value_error(self, limit: object) -> None:
        store = FakeOutboxStore()
        await store.append([_entry("m1")])

        with pytest.raises(ValueError, match="^limit "):
            await store.fetch_unpublished(limit)  # type: ignore[arg-type]


_B79 = "DCE-122 B-79: mark_published leaves a dead-lettered entry dead-lettered"


class TestFakeOutboxStorePublishedWinsAfterDeadLetter:
    @pytest.mark.anyio
    async def test_publication_clears_the_dead_letter(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry("m1")])
        await store.mark_dead_lettered("m1", error="synthetic")

        await store.mark_published(["m1"])

        assert store.is_published("m1")
        assert not store.is_dead_lettered("m1")


class TestFakeOutboxStoreDuplicateMessageId:
    @pytest.mark.anyio
    async def test_duplicate_in_one_batch_stores_nothing(self) -> None:
        store = FakeOutboxStore()

        with pytest.raises(CQRSError):
            await store.append([_entry("m1"), _entry("m2"), _entry("m1")])

        assert store.entries == []
        assert await store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_the_error_names_the_duplicate_message_id(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry("m1")])

        with pytest.raises(
            CQRSError, match="^FakeOutboxStore: duplicate message_id m1$"
        ):
            await store.append([_entry("m2"), _entry("m1")])
