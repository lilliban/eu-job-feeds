"""Greenhouse job boards.

`https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`

Verified against `datadog` (429 postings, full text inline) and against an
invented slug (clean `404 {"status":404,"error":"Job not found"}`).

The `content` field arrives entity-escaped (`&lt;p&gt;`); `html_to_text` undoes
that before stripping tags.
"""

from __future__ import annotations

import logging

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob
from .base import Connector, ProbeResult

log = logging.getLogger(__name__)


class GreenhouseConnector(Connector):
    provider = "greenhouse"

    BASE = "https://boards-api.greenhouse.io/v1/boards"

    def list_url(self, slug: str) -> str:
        return f"{self.BASE}/{slug}/jobs?content=true"

    async def fetch(
        self,
        client: RateLimitedClient,
        slug: str,
        *,
        already_detailed: frozenset[str] = frozenset(),
    ) -> FetchOutcome:
        # This provider ships the advert text in the list response, so there is
        # no per-posting detail call to skip.
        del already_detailed
        try:
            status, payload = await client.get_json(self.list_url(slug))
        except Exception as exc:  # network-level failure
            return self._failed(slug, f"transport: {exc!r}")

        if status == 404:
            # Unambiguous: this board does not exist. Complete, and empty.
            return self._empty(slug)
        if status != 200:
            return self._failed(slug, f"http {status}")
        if not isinstance(payload, dict) or "jobs" not in payload:
            return self._failed(slug, "unexpected payload shape")

        entries = payload.get("jobs")
        if not isinstance(entries, list):
            return self._failed(slug, "jobs is not a list")

        jobs: list[RawJob] = []
        for entry in entries:
            job = self._parse(entry)
            if job is not None:
                jobs.append(job)
        return self._ok(slug, jobs)

    async def probe(self, client: RateLimitedClient, slug: str) -> ProbeResult:
        try:
            status, payload = await client.get_json(self.list_url(slug))
        except Exception:
            return ProbeResult.ERROR
        if status == 404:
            return ProbeResult.NOT_FOUND
        if status == 200 and isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
            # A real board with zero postings still proves the board exists.
            return ProbeResult.FOUND
        return ProbeResult.ERROR

    def _parse(self, entry: object) -> RawJob | None:
        if not isinstance(entry, dict):
            return None
        job_id = entry.get("id")
        title = entry.get("title")
        url = entry.get("absolute_url")
        if job_id is None or not title or not url:
            return None

        location = None
        loc = entry.get("location")
        if isinstance(loc, dict):
            location = loc.get("name")
        if not location:
            offices = entry.get("offices")
            if isinstance(offices, list) and offices and isinstance(offices[0], dict):
                location = offices[0].get("location") or offices[0].get("name")

        department = None
        departments = entry.get("departments")
        if isinstance(departments, list) and departments and isinstance(departments[0], dict):
            department = departments[0].get("name")

        # Greenhouse exposes custom fields as a free-form metadata list. "Time Type"
        # is the one that reliably carries the contract type on the boards checked.
        contract_type = None
        metadata = entry.get("metadata")
        if isinstance(metadata, list):
            for item in metadata:
                if not isinstance(item, dict):
                    continue
                name = (item.get("name") or "").strip().lower()
                value = item.get("value")
                if name in {"time type", "employment type", "job type"} and isinstance(value, str):
                    contract_type = value
                    break

        return RawJob(
            provider=self.provider,
            external_id=str(job_id),
            title=str(title),
            source_url=str(url),
            description_html=entry.get("content") if isinstance(entry.get("content"), str) else None,
            location=location,
            department=department,
            contract_type=contract_type,
            posted_date=entry.get("first_published") or entry.get("updated_at"),
        )
