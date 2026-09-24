"""Dataset writing: stable bytes, readable diffs.

The one-file-per-company layout only pays off if an unchanged company produces
an unchanged file. If the bytes move every run, every commit is a rewrite of the
whole dataset and the git history grows exactly as fast as a single big file
would have.
"""

from __future__ import annotations

import json
from pathlib import Path

from eu_job_feeds.models import JobPosting, seen_stamp
from eu_job_feeds.store import (
    company_path,
    company_relpath,
    load_company,
    write_company,
    write_index,
)


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


class TestStability:
    def test_rewriting_identical_data_reports_no_change(self, tmp_path: Path) -> None:
        jobs = [posting("1"), posting("2")]
        assert write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path) is True
        assert write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path) is False

    def test_bytes_are_identical_across_writes(self, tmp_path: Path) -> None:
        jobs = [posting("1"), posting("2")]
        write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path)
        first = company_path("greenhouse", "acme", tmp_path).read_bytes()
        write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path)
        assert company_path("greenhouse", "acme", tmp_path).read_bytes() == first

    def test_provider_order_does_not_affect_the_file(self, tmp_path: Path) -> None:
        """Providers do not promise a stable order; the file must impose one."""
        jobs = [posting("1"), posting("2"), posting("3")]
        write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path)
        original = company_path("greenhouse", "acme", tmp_path).read_text(encoding="utf-8")

        assert write_company(
            "greenhouse", "acme", "Acme", list(reversed(jobs)), data_dir=tmp_path
        ) is False
        assert company_path("greenhouse", "acme", tmp_path).read_text(encoding="utf-8") == original

    def test_a_changed_timestamp_alone_is_not_a_change(self, tmp_path: Path) -> None:
        """`updated_at` moves every run by definition and must not force a commit."""
        jobs = [posting("1")]
        write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path, updated_at="2026-08-02T00:00:00+00:00")
        assert write_company(
            "greenhouse", "acme", "Acme", jobs, data_dir=tmp_path, updated_at="2026-08-03T00:00:00+00:00"
        ) is False

    def test_a_real_change_is_detected(self, tmp_path: Path) -> None:
        write_company("greenhouse", "acme", "Acme", [posting("1")], data_dir=tmp_path)
        assert write_company(
            "greenhouse", "acme", "Acme", [posting("1"), posting("2")], data_dir=tmp_path
        ) is True

    def test_same_hash_different_url_both_survive(self, tmp_path: Path) -> None:
        """Sorting on the hash alone would make the order of these two arbitrary."""
        a = posting("1", **{"content_hash": "same"})
        b = posting("2", **{"content_hash": "same"})
        write_company("greenhouse", "acme", "Acme", [a, b], data_dir=tmp_path)
        assert len(load_company("greenhouse", "acme", tmp_path)) == 2
        assert write_company("greenhouse", "acme", "Acme", [b, a], data_dir=tmp_path) is False


class TestFormat:
    def test_file_shape(self, tmp_path: Path) -> None:
        write_company("greenhouse", "acme", "Acme", [posting("1")], data_dir=tmp_path)
        raw = json.loads(company_path("greenhouse", "acme", tmp_path).read_text(encoding="utf-8"))
        assert raw["company_name"] == "Acme"
        assert raw["source_board"] == "greenhouse"
        assert raw["job_count"] == 1
        assert isinstance(raw["jobs"], list)

    def test_indentation_and_trailing_newline(self, tmp_path: Path) -> None:
        write_company("greenhouse", "acme", "Acme", [posting("1")], data_dir=tmp_path)
        text = company_path("greenhouse", "acme", tmp_path).read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert '\n  "company_name"' in text

    def test_field_order_is_fixed(self, tmp_path: Path) -> None:
        """The consumer reads by key, but a fixed order keeps diffs readable."""
        write_company("greenhouse", "acme", "Acme", [posting("1")], data_dir=tmp_path)
        raw = json.loads(company_path("greenhouse", "acme", tmp_path).read_text(encoding="utf-8"))
        keys = list(raw["jobs"][0])
        assert keys[:5] == [
            "content_hash", "title", "company_name", "source_url", "source_board",
        ]
        assert keys[-2:] == ["consecutive_misses", "is_closed"]

    def test_open_count_excludes_closed(self, tmp_path: Path) -> None:
        jobs = [posting("1"), posting("2", is_closed=True)]
        write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path)
        raw = json.loads(company_path("greenhouse", "acme", tmp_path).read_text(encoding="utf-8"))
        assert raw["job_count"] == 2
        assert raw["open_job_count"] == 1

    def test_non_ascii_is_written_readably(self, tmp_path: Path) -> None:
        jobs = [posting("1", title="Ingénieur données — München")]
        write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path)
        text = company_path("greenhouse", "acme", tmp_path).read_text(encoding="utf-8")
        assert "München" in text
        assert "\\u" not in text


class TestPaths:
    def test_case_is_preserved(self) -> None:
        """SmartRecruiters' `BoschGroup` is not `boschgroup`."""
        assert company_path("smartrecruiters", "BoschGroup").name == "BoschGroup.json"

    def test_index_path_matches_the_written_path(self) -> None:
        assert company_relpath("smartrecruiters", "BoschGroup") == (
            "smartrecruiters/BoschGroup.json"
        )

    def test_path_separators_cannot_escape_the_directory(self) -> None:
        assert "/" not in company_path("greenhouse", "a/../../b").name


class TestLoad:
    def test_round_trip(self, tmp_path: Path) -> None:
        jobs = [posting("1"), posting("2")]
        write_company("greenhouse", "acme", "Acme", jobs, data_dir=tmp_path)
        loaded = load_company("greenhouse", "acme", tmp_path)
        assert {p.external_id for p in loaded} == {"1", "2"}

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_company("greenhouse", "nobody", tmp_path) == []

    def test_corrupt_file_is_empty_rather_than_fatal(self, tmp_path: Path) -> None:
        path = company_path("greenhouse", "acme", tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_company("greenhouse", "acme", tmp_path) == []

    def test_a_posting_written_before_external_id_existed_still_loads(
        self, tmp_path: Path
    ) -> None:
        path = company_path("greenhouse", "acme", tmp_path)
        path.parent.mkdir(parents=True)
        legacy = posting("1").to_ordered_dict()
        del legacy["external_id"]
        path.write_text(json.dumps({"jobs": [legacy]}), encoding="utf-8")
        loaded = load_company("greenhouse", "acme", tmp_path)
        assert len(loaded) == 1
        assert loaded[0].external_id == ""


class TestIndex:
    def test_index_is_written_and_stable(self, tmp_path: Path) -> None:
        entries = [
            {"company_name": "Acme", "source_board": "greenhouse", "slug": "acme",
             "job_count": 2, "open_job_count": 2, "updated_at": seen_stamp()},
        ]
        assert write_index(entries, data_dir=tmp_path) is True
        assert write_index(entries, data_dir=tmp_path) is False

        raw = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
        assert raw["company_count"] == 1
        assert raw["job_count"] == 2
