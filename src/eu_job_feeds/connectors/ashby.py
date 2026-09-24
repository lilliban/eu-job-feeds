"""Ashby job boards.

`https://api.ashbyhq.com/posting-api/job-board/{slug}`

Verified against `ramp` (2.0 MB of postings) and an invented slug.

Ashby answers a missing board with **`404 text/plain "Not Found"`**, not JSON —
a connector that calls `.json()` unguarded raises instead of concluding "absent".
`RateLimitedClient.get_json` returns `(status, None)` for unparseable bodies, so
the status is checked before the payload.
"""

from __future__ import annotations

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob
from .base import Connector, ProbeResult

# `workplaceType` is a closed vocabulary in Ashby.
_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}


class AshbyConnector(Connector):
    provider = "ashby"

    BASE = "https://api.ashbyhq.com/posting-api/job-board"

    def list_url(self, slug: str) -> str:
        return f"{self.BASE}/{slug}"

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
        except Exception as exc:
            return self._failed(slug, f"transport: {exc!r}")

        if status == 404:
            return self._empty(slug)
        if status != 200:
            return self._failed(slug, f"http {status}")
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            # A 200 whose body is not the expected JSON means something answered
            # that is not the posting API. Never treat that as an empty board.
            return self._failed(slug, "unexpected payload shape")

        jobs = [job for job in (self._parse(e) for e in payload["jobs"]) if job is not None]
        return self._ok(slug, jobs)

    async def probe(self, client: RateLimitedClient, slug: str) -> ProbeResult:
        try:
            status, payload = await client.get_json(self.list_url(slug))
        except Exception:
            return ProbeResult.ERROR
        if status == 404:
            return ProbeResult.NOT_FOUND
        if status == 200 and isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
            return ProbeResult.FOUND
        return ProbeResult.ERROR

    def _parse(self, entry: object) -> RawJob | None:
        if not isinstance(entry, dict):
            return None
        job_id = entry.get("id")
        title = entry.get("title")
        url = entry.get("jobUrl") or entry.get("applyUrl")
        if not job_id or not title or not url:
            return None
        # `isListed: false` postings are not public on the company's own board.
        if entry.get("isListed") is False:
            return None

        location = entry.get("location")
        secondary = entry.get("secondaryLocations")
        if isinstance(secondary, list) and secondary:
            extra = [
                s.get("location")
                for s in secondary
                if isinstance(s, dict) and isinstance(s.get("location"), str)
            ]
            if extra:
                location = ", ".join([str(location)] + extra) if location else ", ".join(extra)

        country = None
        address = entry.get("address")
        if isinstance(address, dict):
            postal = address.get("postalAddress")
            if isinstance(postal, dict):
                country = postal.get("addressCountry")

        work_mode = _WORKPLACE.get(str(entry.get("workplaceType", "")).lower())
        if work_mode is None and entry.get("isRemote") is True:
            work_mode = "remote"

        department = entry.get("department") or entry.get("team")

        return RawJob(
            provider=self.provider,
            external_id=str(job_id),
            title=str(title),
            source_url=str(url),
            description_html=entry.get("descriptionHtml")
            if isinstance(entry.get("descriptionHtml"), str)
            else None,
            description_text=entry.get("descriptionPlain")
            if isinstance(entry.get("descriptionPlain"), str)
            else None,
            location=location if isinstance(location, str) else None,
            country_code=country if isinstance(country, str) else None,
            department=department if isinstance(department, str) else None,
            contract_type=entry.get("employmentType")
            if isinstance(entry.get("employmentType"), str)
            else None,
            posted_date=entry.get("publishedAt")
            if isinstance(entry.get("publishedAt"), str)
            else None,
            work_mode_hint=work_mode,
        )
