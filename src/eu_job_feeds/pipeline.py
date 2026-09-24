"""Run one update: read every registered company, merge, write, summarise."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .connectors import CONNECTORS, WorkdayConnector
from .http import RateLimitedClient
from .lifecycle import MergeStats, already_detailed_ids, merge
from .models import FetchOutcome, seen_stamp
from .registry import CompanyEntry, Registry
from .store import DATA_DIR, company_relpath, load_company, write_company, write_index

log = logging.getLogger(__name__)

#: How many companies are read at once. Spacing is enforced per host anyway, so
#: this only bounds memory and how many hosts are in flight together.
DEFAULT_CONCURRENCY = 6


@dataclass
class RunSummary:
    companies_queried: int = 0
    companies_written: int = 0
    new_jobs: int = 0
    closed_jobs: int = 0
    reopened_jobs: int = 0
    total_jobs: int = 0
    #: provider -> list of "slug: reason" for reads that did not complete.
    failures: dict[str, list[str]] = field(default_factory=dict)
    skipped: int = 0

    def record(self, entry: CompanyEntry, stats: MergeStats) -> None:
        self.companies_queried += 1
        if stats.skipped:
            self.skipped += 1
            reason = stats.errors[0] if stats.errors else "incomplete read"
            self.failures.setdefault(entry.provider, []).append(f"{entry.key}: {reason}")
            return
        self.new_jobs += stats.new
        self.closed_jobs += stats.newly_closed
        self.reopened_jobs += stats.reopened
        self.total_jobs += stats.total

    def as_markdown(self) -> str:
        """The summary appended to the Actions job page."""
        lines = [
            "## eu-job-feeds update",
            "",
            f"- Companies queried: **{self.companies_queried}**",
            f"- Company files changed: **{self.companies_written}**",
            f"- New postings: **{self.new_jobs}**",
            f"- Newly closed: **{self.closed_jobs}**",
            f"- Reopened: **{self.reopened_jobs}**",
            f"- Postings on record: **{self.total_jobs}**",
            f"- Incomplete reads (nothing closed for these): **{self.skipped}**",
        ]
        if self.failures:
            lines += ["", "### Providers that failed", ""]
            for provider, entries in sorted(self.failures.items()):
                lines.append(f"- **{provider}** ({len(entries)}):")
                lines += [f"  - {e}" for e in entries[:10]]
                if len(entries) > 10:
                    lines.append(f"  - …and {len(entries) - 10} more")
        else:
            lines += ["", "No provider failures."]
        return "\n".join(lines) + "\n"


async def _fetch_one(
    client: RateLimitedClient,
    entry: CompanyEntry,
    data_dir: Path,
) -> tuple[CompanyEntry, FetchOutcome, list]:
    stored = load_company(entry.provider, entry.key, data_dir)
    detailed = already_detailed_ids(stored)

    if entry.provider == "workday":
        assert entry.workday is not None  # guaranteed by CompanyEntry validation
        outcome = await WorkdayConnector().fetch(
            client, entry.workday.to_target(), already_detailed=detailed
        )
    else:
        connector = CONNECTORS.get(entry.provider)
        if connector is None:
            outcome = FetchOutcome(
                provider=entry.provider,
                slug=entry.key,
                complete=False,
                error=f"no connector for provider {entry.provider!r}",
            )
        else:
            outcome = await connector.fetch(client, entry.slug or "", already_detailed=detailed)
    return entry, outcome, stored


async def run_update(
    registry: Registry,
    *,
    data_dir: Path = DATA_DIR,
    concurrency: int = DEFAULT_CONCURRENCY,
    client: RateLimitedClient | None = None,
) -> RunSummary:
    """Read every enabled company and write what changed."""
    entries = list(registry.enabled())
    summary = RunSummary()
    stamp = seen_stamp()
    index: list[dict] = []

    owned_client = client is None
    http = client or RateLimitedClient()
    gate = asyncio.Semaphore(concurrency)

    async def one(entry: CompanyEntry) -> None:
        async with gate:
            try:
                _, outcome, stored = await _fetch_one(http, entry, data_dir)
            except Exception as exc:  # a connector bug must not sink the whole run
                log.exception("%s/%s: unhandled error", entry.provider, entry.key)
                outcome = FetchOutcome(
                    provider=entry.provider,
                    slug=entry.key,
                    complete=False,
                    error=f"unhandled: {exc!r}",
                )
                stored = load_company(entry.provider, entry.key, data_dir)

        postings, stats = merge(
            stored, outcome, company_name=entry.name, seen_at=stamp
        )
        summary.record(entry, stats)

        changed = write_company(
            entry.provider,
            entry.key,
            entry.name,
            postings,
            data_dir=data_dir,
            updated_at=stamp,
        )
        if changed:
            summary.companies_written += 1

        index.append(
            {
                "company_name": entry.name,
                "source_board": entry.provider,
                "slug": entry.key,
                "job_count": len(postings),
                "open_job_count": sum(1 for p in postings if not p.is_closed),
                "updated_at": stamp,
                "last_read_complete": not stats.skipped,
                "path": company_relpath(entry.provider, entry.key),
            }
        )

    try:
        await asyncio.gather(*(one(e) for e in entries))
    finally:
        if owned_client:
            await http.aclose()

    write_index(index, data_dir=data_dir)
    return summary
