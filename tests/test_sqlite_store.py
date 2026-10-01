"""SQLite state store: round-trip stability, missing/corrupt files, FTS5.

The archive is rebuilt from scratch every run (see docs/DECISIONS.md #18), so
what matters is that a round trip through `write_database`/`load_all`
reproduces the same postings, that two companies sharing an `external_id`
never collide, and that full-text search actually finds something.
"""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

from eu_job_feeds.models import JobPosting
from eu_job_feeds.sqlite_store import CompanyPostings, compress, decompress, load_all, write_database


def posting(external_id: str, title: str = "Data Analyst", **overrides: object) -> JobPosting:
    base = dict(
        content_hash=f"hash-{external_id}",
        title=title,
        company_name="Acme",
        source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        source_board="greenhouse",
        external_id=external_id,
        first_seen_at="2026-08-01T00:00:00+00:00",
        last_seen_at="2026-08-02T00:00:00+00:00",
    )
    base.update(overrides)
    return JobPosting(**base)


class TestRoundTrip:
    def test_written_postings_are_read_back(self, tmp_path: Path) -> None:
        db_path = tmp_path / "jobs.sqlite"
        jobs = [posting("1"), posting("2")]
        write_database([CompanyPostings("greenhouse", "acme", jobs)], db_path)

        loaded = load_all(db_path)
        assert {p.external_id for p in loaded[("greenhouse", "acme")]} == {"1", "2"}

    def test_languages_round_trip_as_a_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "jobs.sqlite"
        jobs = [posting("1", languages=["en", "de"])]
        write_database([CompanyPostings("greenhouse", "acme", jobs)], db_path)

        loaded = load_all(db_path)[("greenhouse", "acme")]
        assert loaded[0].languages == ["en", "de"]

    def test_is_closed_round_trips_as_a_bool(self, tmp_path: Path) -> None:
        db_path = tmp_path / "jobs.sqlite"
        jobs = [posting("1", is_closed=True)]
        write_database([CompanyPostings("greenhouse", "acme", jobs)], db_path)

        loaded = load_all(db_path)[("greenhouse", "acme")]
        assert loaded[0].is_closed is True

    def test_a_second_rebuild_drops_stale_rows(self, tmp_path: Path) -> None:
        """Full rebuild, not upsert: a posting no longer passed must disappear."""
        db_path = tmp_path / "jobs.sqlite"
        write_database([CompanyPostings("greenhouse", "acme", [posting("1"), posting("2")])], db_path)
        write_database([CompanyPostings("greenhouse", "acme", [posting("1")])], db_path)

        loaded = load_all(db_path)[("greenhouse", "acme")]
        assert {p.external_id for p in loaded} == {"1"}


class TestMissingOrCorrupt:
    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_all(tmp_path / "does-not-exist.sqlite") == {}

    def test_corrupt_file_is_empty_rather_than_fatal(self, tmp_path: Path) -> None:
        db_path = tmp_path / "jobs.sqlite"
        db_path.write_bytes(b"not a sqlite file")
        assert load_all(db_path) == {}

    def test_empty_database_with_no_schema_is_empty(self, tmp_path: Path) -> None:
        """SQLite treats a 0-byte file as a valid, schema-less database."""
        db_path = tmp_path / "jobs.sqlite"
        db_path.touch()
        assert load_all(db_path) == {}


class TestIdentity:
    def test_two_companies_sharing_an_external_id_do_not_collide(self, tmp_path: Path) -> None:
        """The primary key includes `slug`: without it, two Greenhouse orgs
        with numerically-overlapping ids would overwrite each other's rows."""
        db_path = tmp_path / "jobs.sqlite"
        a = posting("1", title="Role at Acme")
        b = posting("1", title="Role at Zenith")
        write_database(
            [
                CompanyPostings("greenhouse", "acme", [a]),
                CompanyPostings("greenhouse", "zenith", [b]),
            ],
            db_path,
        )

        loaded = load_all(db_path)
        assert loaded[("greenhouse", "acme")][0].title == "Role at Acme"
        assert loaded[("greenhouse", "zenith")][0].title == "Role at Zenith"

    def test_a_duplicate_external_id_within_one_company_does_not_abort_the_write(
        self, tmp_path: Path
    ) -> None:
        """Observed on real data (SmartRecruiters, Workable): a connector can
        return the same external_id twice in one fetch. The old JSON array
        tolerated it silently; the write must not crash over it either."""
        db_path = tmp_path / "jobs.sqlite"
        jobs = [posting("1", title="First copy"), posting("1", title="Second copy")]
        write_database([CompanyPostings("greenhouse", "acme", jobs)], db_path)

        loaded = load_all(db_path)[("greenhouse", "acme")]
        assert len(loaded) == 1
        assert loaded[0].title == "Second copy"  # last occurrence wins

    def test_external_id_missing_falls_back_to_source_url_as_the_row_key(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "jobs.sqlite"
        jobs = [posting("", source_url="https://boards.greenhouse.io/acme/jobs/legacy")]
        write_database([CompanyPostings("greenhouse", "acme", jobs)], db_path)

        loaded = load_all(db_path)[("greenhouse", "acme")]
        assert len(loaded) == 1
        assert loaded[0].source_url.endswith("/legacy")


class TestFullTextSearch:
    def test_fts5_is_available(self) -> None:
        """A runner without FTS5 compiled into its sqlite3 build must fail
        here, in CI, not later against production data."""
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            conn.close()

    def test_a_search_hit_matches_the_description(self, tmp_path: Path) -> None:
        db_path = tmp_path / "jobs.sqlite"
        jobs = [
            posting("1", description="We need a Python engineer."),
            posting("2", description="Looking for a barista."),
        ]
        write_database([CompanyPostings("greenhouse", "acme", jobs)], db_path)

        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT postings.row_key FROM postings_fts "
                "JOIN postings ON postings.rowid = postings_fts.rowid "
                "WHERE postings_fts MATCH 'python'"
            ).fetchall()
        finally:
            conn.close()
        assert [r[0] for r in rows] == ["1"]


class TestCompression:
    def test_round_trip_through_gzip(self, tmp_path: Path) -> None:
        db_path = tmp_path / "jobs.sqlite"
        write_database([CompanyPostings("greenhouse", "acme", [posting("1")])], db_path)

        gz_path = compress(db_path)
        assert gz_path.exists()
        with gzip.open(gz_path, "rb") as fh:
            assert fh.read(16).startswith(b"SQLite format 3")

        restored_path = tmp_path / "restored.sqlite"
        decompress(gz_path, restored_path)
        assert load_all(restored_path) == load_all(db_path)
