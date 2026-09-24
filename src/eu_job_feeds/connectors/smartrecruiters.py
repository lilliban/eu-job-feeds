"""SmartRecruiters job boards.

List:   `https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=N`
Detail: `https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}`

Verified against `SmartRecruiters` (8 postings) and `BoschGroup` (4713).

Two traps here, both silent:

* **A missing company answers `200` with an empty list**, exactly like a real
  company with no open roles. Probing can therefore only ever return AMBIGUOUS,
  and a slug for this provider has to be confirmed by a human.
* **The company identifier is case-sensitive**, which is documented nowhere:
  `Bosch` returns 0 postings, `BoschGroup` returns 4713. The slug is treated as
  opaque and never normalised.

The list response carries no advert text, so each posting needs one extra
request. Bosch alone would be 4713 of them per run, which is why
`already_detailed` exists.
"""

from __future__ import annotations

import logging

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob
from .base import Connector, ProbeResult

log = logging.getLogger(__name__)

PAGE_SIZE = 100
#: Hard ceiling on detail requests per company per run. A first run against a
#: 4713-posting board fills the text in over several runs instead of spending
#: forty minutes in one job.
DETAIL_BUDGET = 250


class SmartRecruitersConnector(Connector):
    provider = "smartrecruiters"

    BASE = "https://api.smartrecruiters.com/v1/companies"

    def list_url(self, slug: str, offset: int = 0, limit: int = PAGE_SIZE) -> str:
        return f"{self.BASE}/{slug}/postings?limit={limit}&offset={offset}"

    def detail_url(self, slug: str, posting_id: str) -> str:
        return f"{self.BASE}/{slug}/postings/{posting_id}"

    def posting_url(self, slug: str, posting_id: str) -> str:
        return f"https://jobs.smartrecruiters.com/{slug}/{posting_id}"

    async def fetch(
        self,
        client: RateLimitedClient,
        slug: str,
        *,
        already_detailed: frozenset[str] = frozenset(),
    ) -> FetchOutcome:
        entries: list[dict] = []
        offset = 0
        total: int | None = None

        while True:
            try:
                status, payload = await client.get_json(self.list_url(slug, offset))
            except Exception as exc:
                return self._failed(slug, f"transport at offset {offset}: {exc!r}")
            if status != 200:
                return self._failed(slug, f"http {status} at offset {offset}")
            if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
                return self._failed(slug, "unexpected payload shape")

            page = payload["content"]
            entries.extend(e for e in page if isinstance(e, dict))
            if total is None:
                total = payload.get("totalFound")
                total = total if isinstance(total, int) else None

            offset += PAGE_SIZE
            if not page or (total is not None and len(entries) >= total):
                break
            if offset > 20_000:  # runaway guard; no real board is this large
                return self._failed(slug, "pagination exceeded 20000 postings")

        # A short read means the catalogue was not fully walked. Reporting it as
        # complete would let the lifecycle close everything past the cut-off.
        if total is not None and len(entries) < total:
            return self._failed(slug, f"got {len(entries)} of {total} postings")

        jobs: list[RawJob] = []
        spent = 0
        for entry in entries:
            job = self._parse(entry, slug)
            if job is None:
                continue
            if job.external_id not in already_detailed and spent < DETAIL_BUDGET:
                spent += 1
                await self._add_detail(client, slug, job)
            jobs.append(job)

        if spent >= DETAIL_BUDGET:
            log.info(
                "%s/%s: detail budget of %d spent, remaining adverts fill in on later runs",
                self.provider, slug, DETAIL_BUDGET,
            )
        # `complete` reflects the list walk only: every posting is accounted for,
        # even the ones whose text has not been fetched yet.
        return self._ok(slug, jobs)

    async def probe(self, client: RateLimitedClient, slug: str) -> ProbeResult:
        try:
            status, payload = await client.get_json(self.list_url(slug, limit=1))
        except Exception:
            return ProbeResult.ERROR
        if status != 200 or not isinstance(payload, dict):
            return ProbeResult.ERROR
        if not isinstance(payload.get("content"), list):
            return ProbeResult.ERROR
        if payload["content"]:
            return ProbeResult.FOUND
        # An empty list proves nothing: unknown company and idle company are
        # indistinguishable here. Never cache this as "not found".
        return ProbeResult.AMBIGUOUS

    async def _add_detail(self, client: RateLimitedClient, slug: str, job: RawJob) -> None:
        """Fill in the advert text. A failure here leaves the posting textless."""
        try:
            status, payload = await client.get_json(self.detail_url(slug, job.external_id))
        except Exception:
            return
        if status != 200 or not isinstance(payload, dict):
            return

        url = payload.get("postingUrl") or payload.get("applyUrl")
        if isinstance(url, str) and url:
            job.source_url = url

        job_ad = payload.get("jobAd")
        sections = job_ad.get("sections") if isinstance(job_ad, dict) else None
        if not isinstance(sections, dict):
            return

        def section(name: str) -> str | None:
            item = sections.get(name)
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return item["text"].strip() or None
            return None

        # `qualifications` is the requirements half by SmartRecruiters' own
        # definition, so it maps straight across and needs no heading heuristic.
        description_parts = [section("companyDescription"), section("jobDescription")]
        job.description_html = "\n".join(p for p in description_parts if p) or None
        job.requirements_html = section("qualifications")

    def _parse(self, entry: dict, slug: str) -> RawJob | None:
        posting_id = entry.get("id")
        title = entry.get("name")
        if not posting_id or not title:
            return None

        location = entry.get("location") if isinstance(entry.get("location"), dict) else {}
        city = location.get("city") if isinstance(location.get("city"), str) else None
        country = location.get("country") if isinstance(location.get("country"), str) else None
        full = location.get("fullLocation") if isinstance(location.get("fullLocation"), str) else None

        work_mode = None
        if location.get("remote") is True:
            work_mode = "remote"
        elif location.get("hybrid") is True:
            work_mode = "hybrid"

        def label(key: str) -> str | None:
            item = entry.get(key)
            if isinstance(item, dict) and isinstance(item.get("label"), str):
                return item["label"]
            return None

        return RawJob(
            provider=self.provider,
            external_id=str(posting_id),
            title=str(title),
            # Replaced with the real `postingUrl` when the detail call succeeds.
            source_url=self.posting_url(slug, str(posting_id)),
            location=full or city,
            city=city,
            country_code=country,
            department=label("department") or label("function"),
            contract_type=label("typeOfEmployment"),
            seniority=label("experienceLevel"),
            posted_date=entry.get("releasedDate")
            if isinstance(entry.get("releasedDate"), str)
            else None,
            work_mode_hint=work_mode,
        )
