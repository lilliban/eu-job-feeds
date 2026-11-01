"""Workable job boards.

`https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true`

Verified against `amazingcarecareers` (288 postings) and an invented slug.

Two things differ from the brief:

* **A missing account answers `404 text/plain "Not Found"`**, not `200` with an
  empty list. The `200 {"jobs": []}` case is real but means something else: an
  account that exists and has no open roles — `deliveroo`, `typeform`,
  `hubspot`, `wetransfer` and `aircall` all answer that way. Workable is
  therefore fully disambiguable in discovery, unlike SmartRecruiters.
* **`?details=true` returns every advert body in a single response**, so no
  per-posting detail call is needed. Without it the list has no text at all.

`requirements` and `benefits` are frequently null even with details on.
"""

from __future__ import annotations

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob
from .base import Connector, ProbeResult


class WorkableConnector(Connector):
    provider = "workable"

    BASE = "https://apply.workable.com/api/v1/widget/accounts"

    def list_url(self, slug: str) -> str:
        return f"{self.BASE}/{slug}?details=true"

    async def fetch(
        self,
        client: RateLimitedClient,
        slug: str,
        *,
        already_detailed: frozenset[str] = frozenset(),
    ) -> FetchOutcome:
        # `?details=true` already carries every body, so nothing is deferred.
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
            # An account with zero postings still proves the account exists.
            return ProbeResult.FOUND
        return ProbeResult.ERROR

    def account_name(self, payload: object) -> str | None:
        """The display name Workable holds, used only to seed a registry entry."""
        if isinstance(payload, dict) and isinstance(payload.get("name"), str):
            return payload["name"].strip() or None
        return None

    def _parse(self, entry: object) -> RawJob | None:
        if not isinstance(entry, dict):
            return None
        shortcode = entry.get("shortcode")
        title = entry.get("title")
        url = entry.get("url") or entry.get("shortlink") or entry.get("application_url")
        if not shortcode or not title or not url:
            return None

        city = entry.get("city") if isinstance(entry.get("city"), str) else None
        country = entry.get("country") if isinstance(entry.get("country"), str) else None
        state = entry.get("state") if isinstance(entry.get("state"), str) else None

        # `locations[]` carries a proper ISO country code; the top-level `country`
        # is a display name like "United States".
        locations = entry.get("locations")
        if isinstance(locations, list) and locations and isinstance(locations[0], dict):
            code = locations[0].get("countryCode")
            if isinstance(code, str) and code:
                country = code
            city = city or (locations[0].get("city") if isinstance(locations[0].get("city"), str) else None)

        location = ", ".join(p for p in (city, state, entry.get("country")) if isinstance(p, str) and p)

        work_mode = "remote" if entry.get("telecommuting") is True else None

        return RawJob(
            provider=self.provider,
            external_id=str(shortcode),
            title=str(title),
            source_url=str(url),
            description_html=entry.get("description")
            if isinstance(entry.get("description"), str)
            else None,
            requirements_html=entry.get("requirements")
            if isinstance(entry.get("requirements"), str)
            else None,
            location=location or None,
            city=city,
            country_code=country,
            department=entry.get("department")
            if isinstance(entry.get("department"), str)
            else None,
            contract_type=entry.get("employment_type")
            if isinstance(entry.get("employment_type"), str)
            else None,
            seniority=entry.get("experience") if isinstance(entry.get("experience"), str) else None,
            posted_date=entry.get("published_on")
            if isinstance(entry.get("published_on"), str)
            else None,
            work_mode_hint=work_mode,
        )
