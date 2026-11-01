"""End-to-end wiring: `run_update` reads the registry, writes `jobs.sqlite`
and `changes.json`, and its `RunSummary` matches what actually happened.
"""

from __future__ import annotations

import json
from pathlib import Path

from eu_job_feeds import pipeline, sqlite_store
from eu_job_feeds.http import RateLimitedClient
from eu_job_feeds.models import FetchOutcome, RawJob
from eu_job_feeds.registry import CompanyEntry, Registry


class FakeConnector:
    """Stands in for a real `Connector`: returns canned jobs, makes no requests."""

    provider = "greenhouse"

    def __init__(self, jobs: list[RawJob]) -> None:
        self.jobs = jobs

    def list_url(self, slug: str) -> str:
        return f"https://example.test/{slug}"

    async def fetch(self, client, slug, *, already_detailed=frozenset()) -> FetchOutcome:
        return FetchOutcome(provider="greenhouse", slug=slug, complete=True, jobs=self.jobs)


def raw(external_id: str) -> RawJob:
    return RawJob(
        provider="greenhouse",
        external_id=external_id,
        title="Data Analyst",
        source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        description_text="We are hiring.",
    )


async def test_run_update_writes_the_database_and_the_diff(tmp_path: Path, monkeypatch) -> None:
    fake = FakeConnector([raw("1")])
    monkeypatch.setitem(pipeline.CONNECTORS, "greenhouse", fake)

    registry = Registry(companies=[CompanyEntry(name="Acme", provider="greenhouse", slug="acme")])
    db_path = tmp_path / "jobs.sqlite"
    changes_path = tmp_path / "changes.json"
    data_dir = tmp_path / "data"

    async with RateLimitedClient() as client:
        summary = await pipeline.run_update(
            registry,
            data_dir=data_dir,
            db_path=db_path,
            changes_path=changes_path,
            client=client,
        )

    assert summary.companies_queried == 1
    assert summary.new_jobs == 1

    stored = sqlite_store.load_all(db_path)
    assert {p.external_id for p in stored[("greenhouse", "acme")]} == {"1"}

    changes = json.loads(changes_path.read_text(encoding="utf-8"))
    assert changes["new_count"] == 1
    assert (data_dir / "index.json").exists()


async def test_a_second_run_with_the_same_postings_reports_nothing_new(
    tmp_path: Path, monkeypatch
) -> None:
    """Prior state comes from `jobs.sqlite`, not from a JSON file: a second run
    against the same database must recognise `1` as still open, not new."""
    fake = FakeConnector([raw("1")])
    monkeypatch.setitem(pipeline.CONNECTORS, "greenhouse", fake)

    registry = Registry(companies=[CompanyEntry(name="Acme", provider="greenhouse", slug="acme")])
    db_path = tmp_path / "jobs.sqlite"
    data_dir = tmp_path / "data"

    async with RateLimitedClient() as client:
        await pipeline.run_update(registry, data_dir=data_dir, db_path=db_path, client=client)
    async with RateLimitedClient() as client:
        summary = await pipeline.run_update(registry, data_dir=data_dir, db_path=db_path, client=client)

    assert summary.new_jobs == 0
    assert summary.total_jobs == 1
