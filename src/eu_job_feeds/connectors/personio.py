"""Personio job boards.

`https://{slug}.jobs.personio.de/xml`

**This is the connector that must not trust its status code.** A slug that does
not exist does not 404: it redirects to `www.personio.com` and answers `200`
with tens of kilobytes of marketing HTML. Measured while building this: an
invented slug landed on `https://personio.com` with a 31 KB Astro page. A
connector keyed on the status code would announce that every company on earth
has a Personio board.

The check is therefore structural: **the parsed XML root element must be
`workzag-jobs`**. Anything else — HTML, a redirect body, an empty document — is
"no board here".

Probing Personio is also rate-limited hard: a handful of consecutive unknown
slugs starts returning 429, which is why `RateLimitedClient` spaces this host at
two seconds.
"""

from __future__ import annotations

import logging
import re
from xml.etree import ElementTree

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob
from ..normalize.experience import parse_structured_range
from .base import Connector, ProbeResult

log = logging.getLogger(__name__)

ROOT_TAG = "workzag-jobs"

# Personio's own vocabularies.
_SCHEDULE = {
    "full-time": "Full-time",
    "part-time": "Part-time",
    "full-or-part-time": "Full or part time",
}
_EMPLOYMENT = {
    "permanent": "Permanent",
    "intern": "Internship",
    "trainee": "Trainee",
    "freelance": "Freelance",
    "working-student": "Working student",
    "apprentice": "Apprenticeship",
    "temporary": "Temporary",
}
_SENIORITY = {
    "student": "Student",
    "entry-level": "Entry level",
    "experienced": "Experienced",
    "senior": "Senior",
    "lead": "Lead",
    "executive": "Executive",
    "director": "Director",
}

# An XML document that declares entities is not something a job board needs, and
# ElementTree expands internal entities. Cheap structural refusal.
_UNSAFE_XML = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class PersonioConnector(Connector):
    provider = "personio"

    def list_url(self, slug: str) -> str:
        return f"https://{slug}.jobs.personio.de/xml"

    def job_url(self, slug: str, job_id: str) -> str:
        return f"https://{slug}.jobs.personio.de/job/{job_id}"

    async def fetch(
        self,
        client: RateLimitedClient,
        slug: str,
        *,
        already_detailed: frozenset[str] = frozenset(),
    ) -> FetchOutcome:
        del already_detailed
        try:
            resp = await client.request("GET", self.list_url(slug))
        except Exception as exc:
            return self._failed(slug, f"transport: {exc!r}")

        if resp.status_code == 404:
            return self._empty(slug)
        if resp.status_code != 200:
            # 429 in particular: a throttled read tells us nothing about the board.
            return self._failed(slug, f"http {resp.status_code}")

        root = self._parse_root(resp.text)
        if root is None:
            # Trap 1: a 200 that is not a workzag-jobs document means the slug
            # does not exist and Personio served its marketing site instead.
            return self._empty(slug)

        jobs = [
            job
            for job in (self._parse(position, slug) for position in root.findall("position"))
            if job is not None
        ]
        return self._ok(slug, jobs)

    async def probe(self, client: RateLimitedClient, slug: str) -> ProbeResult:
        try:
            resp = await client.request("GET", self.list_url(slug))
        except Exception:
            return ProbeResult.ERROR
        if resp.status_code == 404:
            return ProbeResult.NOT_FOUND
        if resp.status_code != 200:
            return ProbeResult.ERROR
        return ProbeResult.FOUND if self._parse_root(resp.text) is not None else ProbeResult.NOT_FOUND

    def _parse_root(self, body: str) -> ElementTree.Element | None:
        """Return the root element only if this really is a Personio feed."""
        if not body or _UNSAFE_XML.search(body[:4096]):
            return None
        # Cheap check before paying for a parse of a 1.7 MB marketing page.
        if ROOT_TAG not in body[:2048]:
            return None
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            return None
        return root if root.tag == ROOT_TAG else None

    def _parse(self, position: ElementTree.Element, slug: str) -> RawJob | None:
        def text(tag: str) -> str | None:
            node = position.find(tag)
            if node is None or node.text is None:
                return None
            return node.text.strip() or None

        job_id = text("id")
        title = text("name")
        if not job_id or not title:
            return None

        offices = [o for o in (text("office"),) if o]
        additional = position.find("additionalOffices")
        if additional is not None:
            offices += [o.text.strip() for o in additional.findall("office") if o.text and o.text.strip()]

        # <jobDescriptions> holds <jobDescription><name/><value/></jobDescription>
        # pairs. The name is a heading, so the body is rebuilt with the headings
        # intact and the requirements splitter takes it from there.
        body_parts: list[str] = []
        descriptions = position.find("jobDescriptions")
        if descriptions is not None:
            for item in descriptions.findall("jobDescription"):
                name_node = item.find("name")
                value_node = item.find("value")
                heading = (name_node.text or "").strip() if name_node is not None else ""
                value = (value_node.text or "").strip() if value_node is not None else ""
                if heading:
                    body_parts.append(f"<h3>{heading}</h3>")
                if value:
                    body_parts.append(value)

        min_years, max_years = parse_structured_range(text("yearsOfExperience"))

        return RawJob(
            provider=self.provider,
            external_id=job_id,
            title=title,
            source_url=self.job_url(slug, job_id),
            description_html="\n".join(body_parts) or None,
            location=", ".join(offices) or None,
            city=offices[0] if offices else None,
            department=text("department") or text("recruitingCategory"),
            contract_type=_SCHEDULE.get((text("schedule") or "").lower())
            or _EMPLOYMENT.get((text("employmentType") or "").lower()),
            seniority=_SENIORITY.get((text("seniority") or "").lower()),
            posted_date=text("createdAt"),
            min_years_exp_hint=min_years,
            max_years_exp_hint=max_years,
        )
