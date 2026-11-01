"""Zefix snapshot/queue store.

`write_snapshot` is an upsert, not a full rebuild like `sqlite_store`'s
postings table: `processed_at` is the discovery queue, and a weekly re-sync
must never reset it for a company that is still active — only a brand new
UID should start with `processed_at IS NULL`.
"""

from __future__ import annotations

from pathlib import Path

from eu_job_feeds.zefix import ZefixCompany
from eu_job_feeds.zefix_store import (
    mark_processed,
    next_batch,
    queue_stats,
    write_snapshot,
)

AT = "2026-09-01T00:00:00+00:00"


def company(uid: str, name: str = "Acme GmbH") -> ZefixCompany:
    return ZefixCompany(uid=uid, legal_name=name, legal_form="GmbH/Sagl")


class TestWriteSnapshot:
    def test_a_first_sync_adds_everything(self, tmp_path: Path) -> None:
        db = tmp_path / "zefix.sqlite"
        stats = write_snapshot({"CHE1": company("CHE1")}, db, at=AT)
        assert stats.added == 1
        assert stats.removed == 0
        assert stats.unchanged == 0

    def test_a_company_no_longer_active_is_removed(self, tmp_path: Path) -> None:
        db = tmp_path / "zefix.sqlite"
        write_snapshot({"CHE1": company("CHE1"), "CHE2": company("CHE2")}, db, at=AT)
        stats = write_snapshot({"CHE1": company("CHE1")}, db, at=AT)
        assert stats.removed == 1
        assert next_batch(db, size=10) == [company("CHE1")]

    def test_a_resync_preserves_processed_at_for_still_active_companies(
        self, tmp_path: Path
    ) -> None:
        """The whole point of an upsert here: a company already probed must not
        re-enter the discovery queue just because Zefix was pulled again."""
        db = tmp_path / "zefix.sqlite"
        write_snapshot({"CHE1": company("CHE1")}, db, at=AT)
        mark_processed(db, ["CHE1"], at=AT)

        write_snapshot({"CHE1": company("CHE1")}, db, at="2026-09-08T00:00:00+00:00")

        assert next_batch(db, size=10) == []  # still processed, not back in the queue

    def test_a_resync_updates_the_name_on_conflict(self, tmp_path: Path) -> None:
        db = tmp_path / "zefix.sqlite"
        write_snapshot({"CHE1": company("CHE1", "Old Name AG")}, db, at=AT)
        write_snapshot({"CHE1": company("CHE1", "New Name AG")}, db, at=AT)

        assert next_batch(db, size=10)[0].legal_name == "New Name AG"


class TestQueue:
    def test_next_batch_only_returns_unprocessed(self, tmp_path: Path) -> None:
        db = tmp_path / "zefix.sqlite"
        write_snapshot({"CHE1": company("CHE1"), "CHE2": company("CHE2")}, db, at=AT)
        mark_processed(db, ["CHE1"], at=AT)

        assert [c.uid for c in next_batch(db, size=10)] == ["CHE2"]

    def test_next_batch_respects_size(self, tmp_path: Path) -> None:
        db = tmp_path / "zefix.sqlite"
        write_snapshot(
            {f"CHE{i}": company(f"CHE{i}") for i in range(5)}, db, at=AT
        )
        assert len(next_batch(db, size=2)) == 2

    def test_next_batch_on_a_missing_database_is_empty(self, tmp_path: Path) -> None:
        assert next_batch(tmp_path / "does-not-exist.sqlite", size=10) == []

    def test_queue_stats_reports_pending_and_processed(self, tmp_path: Path) -> None:
        db = tmp_path / "zefix.sqlite"
        write_snapshot({"CHE1": company("CHE1"), "CHE2": company("CHE2")}, db, at=AT)
        mark_processed(db, ["CHE1"], at=AT)

        stats = queue_stats(db)
        assert stats == {"total": 2, "processed": 1, "pending": 1}

    def test_queue_stats_on_a_missing_database_is_zero(self, tmp_path: Path) -> None:
        assert queue_stats(tmp_path / "does-not-exist.sqlite") == {
            "total": 0,
            "processed": 0,
            "pending": 0,
        }

    def test_mark_processed_with_no_uids_is_a_no_op(self, tmp_path: Path) -> None:
        db = tmp_path / "zefix.sqlite"
        write_snapshot({"CHE1": company("CHE1")}, db, at=AT)
        mark_processed(db, [], at=AT)  # must not raise on a missing/empty db either
        assert queue_stats(db)["pending"] == 1
