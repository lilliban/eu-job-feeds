"""Aggregate stats: `data/index.json` writing is stable and idempotent.

The postings themselves live in `state/jobs.sqlite` now (see
`sqlite_store.py` and docs/DECISIONS.md #18) — this module only writes the
lightweight per-company summary and dataset-wide counters that stay in git.
"""

from __future__ import annotations

import json
from pathlib import Path

from eu_job_feeds.models import seen_stamp
from eu_job_feeds.store import write_index


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

    def test_a_changed_timestamp_alone_is_not_a_change(self, tmp_path: Path) -> None:
        """`generated_at`/`updated_at` move every run by definition and must
        not force a commit."""
        entries = [
            {"company_name": "Acme", "source_board": "greenhouse", "slug": "acme",
             "job_count": 1, "open_job_count": 1, "updated_at": "2026-08-02T00:00:00+00:00"},
        ]
        write_index(entries, data_dir=tmp_path)
        entries[0]["updated_at"] = "2026-08-03T00:00:00+00:00"
        assert write_index(entries, data_dir=tmp_path) is False

    def test_a_real_change_is_detected(self, tmp_path: Path) -> None:
        entries = [
            {"company_name": "Acme", "source_board": "greenhouse", "slug": "acme",
             "job_count": 1, "open_job_count": 1, "updated_at": seen_stamp()},
        ]
        write_index(entries, data_dir=tmp_path)
        entries.append(
            {"company_name": "Zenith", "source_board": "greenhouse", "slug": "zenith",
             "job_count": 1, "open_job_count": 1, "updated_at": seen_stamp()}
        )
        assert write_index(entries, data_dir=tmp_path) is True
