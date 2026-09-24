"""Workday job boards.

List:   `POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`
Detail: `GET  https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}`

Verified against `nvidia` / `wd5` / `NVIDIAExternalCareerSite` (total 2000).

Deliberately **not** a `Connector`. Every other provider identifies a company by
one opaque slug; Workday needs three independent parts — the tenant, the data
centre number (`wd1`…`wd5`), and the career-site name — and none of them can be
derived from the others. Forcing that into a single-slug abstraction would mean
encoding a tuple into a string and parsing it back everywhere.

The parts are also not guessable, which is why they are curated by hand in the
registry: a wrong site name on the right tenant answers `422`, and some tenants
answer `401` regardless. Both were observed on real tenants (`sap`, `adidas`,
`siemens`).

Cost: **`limit` is capped at 20** — anything larger is a `400` — so NVIDIA's
2000 openings are 100 POSTs, plus one GET per advert body. `already_detailed`
and `DETAIL_BUDGET` keep steady-state runs cheap.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob

log = logging.getLogger(__name__)

#: Workday rejects a larger page with HTTP 400. Not a tuning knob.
PAGE_SIZE = 20
DETAIL_BUDGET = 200

_WD_RE = re.compile(r"^wd\d+$")


@dataclass(frozen=True)
class WorkdayTarget:
    """The three parts that identify one Workday career site."""

    tenant: str
    wd: str
    site: str

    def __post_init__(self) -> None:
        if not _WD_RE.match(self.wd):
            raise ValueError(f"wd must look like 'wd5', got {self.wd!r}")

    @property
    def host(self) -> str:
        return f"https://{self.tenant}.{self.wd}.myworkdayjobs.com"

    @property
    def slug(self) -> str:
        """Filename stem in `data/workday/`. The tenant identifies the company."""
        return self.tenant

    def list_url(self) -> str:
        return f"{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def detail_url(self, external_path: str) -> str:
        return f"{self.host}/wday/cxs/{self.tenant}/{self.site}{external_path}"

    def public_url(self, external_path: str) -> str:
        return f"{self.host}/{self.site}{external_path}"


class WorkdayConnector:
    """Reads one Workday career site."""

    provider = "workday"

    async def fetch(
        self,
        client: RateLimitedClient,
        target: WorkdayTarget,
        *,
        already_detailed: frozenset[str] = frozenset(),
    ) -> FetchOutcome:
        postings: list[dict] = []
        offset = 0
        total: int | None = None

        while True:
            body = {
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            }
            try:
                status, payload = await client.post_json(target.list_url(), body)
            except Exception as exc:
                return self._failed(target, f"transport at offset {offset}: {exc!r}")

            if status in (401, 403):
                return self._failed(target, f"http {status}: site is not publicly readable")
            if status == 422:
                # Right tenant, wrong site name. A registry error, not an outage.
                return self._failed(target, "http 422: site name rejected by tenant")
            if status != 200:
                return self._failed(target, f"http {status} at offset {offset}")
            if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
                return self._failed(target, "unexpected payload shape")

            page = payload["jobPostings"]
            postings.extend(p for p in page if isinstance(p, dict))
            if total is None and isinstance(payload.get("total"), int):
                total = payload["total"]

            offset += PAGE_SIZE
            if not page or (total is not None and len(postings) >= total):
                break
            if offset > 20_000:
                return self._failed(target, "pagination exceeded 20000 postings")

        if total is not None and len(postings) < total:
            return self._failed(target, f"got {len(postings)} of {total} postings")

        jobs: list[RawJob] = []
        spent = 0
        for entry in postings:
            job = self._parse(entry, target)
            if job is None:
                continue
            if job.external_id not in already_detailed and spent < DETAIL_BUDGET:
                spent += 1
                await self._add_detail(client, target, job, entry.get("externalPath"))
            jobs.append(job)

        if spent >= DETAIL_BUDGET:
            log.info(
                "workday/%s: detail budget of %d spent, remaining adverts fill in on later runs",
                target.slug, DETAIL_BUDGET,
            )
        return FetchOutcome(
            provider=self.provider, slug=target.slug, complete=True, jobs=jobs
        )

    async def _add_detail(
        self,
        client: RateLimitedClient,
        target: WorkdayTarget,
        job: RawJob,
        external_path: object,
    ) -> None:
        if not isinstance(external_path, str) or not external_path:
            return
        try:
            status, payload = await client.get_json(target.detail_url(external_path))
        except Exception:
            return
        if status != 200 or not isinstance(payload, dict):
            return
        info = payload.get("jobPostingInfo")
        if not isinstance(info, dict):
            return

        if isinstance(info.get("jobDescription"), str):
            job.description_html = info["jobDescription"]
        if isinstance(info.get("externalUrl"), str) and info["externalUrl"]:
            job.source_url = info["externalUrl"]
        if isinstance(info.get("timeType"), str):
            job.contract_type = info["timeType"]
        # `postedOn` is relative text ("Posted Today"); `startDate` is a real date.
        if isinstance(info.get("startDate"), str):
            job.posted_date = info["startDate"]
        country = info.get("country")
        if isinstance(country, dict) and isinstance(country.get("descriptor"), str):
            job.country_code = country["descriptor"]

    def _parse(self, entry: dict, target: WorkdayTarget) -> RawJob | None:
        title = entry.get("title")
        external_path = entry.get("externalPath")
        if not title or not isinstance(external_path, str) or not external_path:
            return None

        location = entry.get("locationsText")
        return RawJob(
            provider=self.provider,
            # The path is the only identifier present in the list response and it
            # is stable across runs; the requisition id is not always in bulletFields.
            external_id=external_path,
            title=str(title),
            source_url=target.public_url(external_path),
            location=location if isinstance(location, str) else None,
        )

    def _failed(self, target: WorkdayTarget, error: str) -> FetchOutcome:
        return FetchOutcome(
            provider=self.provider, slug=target.slug, complete=False, error=error
        )
