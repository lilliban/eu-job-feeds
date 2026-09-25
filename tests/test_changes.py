"""The run's diff file: new and closed postings, written every run.

Unlike `store.write_company`, this file has no "nothing changed" shortcut —
it is never committed to git, so an unconditional overwrite costs nothing and
never leaves a downstream consumer reading a stale document.
"""

from __future__ import annotations

import json
from pathlib import Path

from eu_job_feeds.changes import write_changes
from eu_job_feeds.models import JobPosting


def posting(external_id: str, **overrides: object) -> JobPosting:
    base = dict(
        content_hash=f"hash-{external_id}",
        title="Data Analyst",
        company_name="Acme",
        source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        source_board="greenhouse",
        external_id=external_id,
        first_seen_at="2026-08-01T00:00:00+00:00",
        last_seen_at="2026-08-02T00:00:00+00:00",
    )
    base.update(overrides)
    return JobPosting(**base)


class TestWriteChanges:
    def test_new_and_closed_are_recorded_separately(self, tmp_path: Path) -> None:
        path = tmp_path / "changes.json"
        write_changes([posting("1")], [posting("2")], path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["new_count"] == 1
        assert raw["closed_count"] == 1
        assert raw["new"][0]["external_id"] == "1"
        assert raw["closed"][0]["external_id"] == "2"

    def test_an_empty_run_still_writes_a_valid_document(self, tmp_path: Path) -> None:
        path = tmp_path / "changes.json"
        write_changes([], [], path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["new"] == []
        assert raw["closed"] == []

    def test_a_later_call_replaces_the_content(self, tmp_path: Path) -> None:
        """No stability short-circuit like `store.write_company`: the file
        always reflects the most recent call."""
        path = tmp_path / "changes.json"
        write_changes([posting("1")], [], path)
        write_changes([posting("2")], [], path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["new"][0]["external_id"] == "2"
