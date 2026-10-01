"""Recruitee job boards.

`https://{slug}.recruitee.com/api/offers/`

Verified against `channable` (12 offers) and an invented slug (clean 404 JSON).

The richest provider of the set: it separates `description` from `requirements`,
and ships `city`, `country_code`, `employment_type_code` and a structured salary
with an explicit period. Almost nothing here needs a regex.
"""

from __future__ import annotations

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob
from ..normalize.salary import ALLOWED_CURRENCIES, MAX_PLAUSIBLE, MIN_PLAUSIBLE
from .base import Connector, ProbeResult

# Recruitee's own vocabulary, mapped to readable text for `contract_type`.
# The normaliser turns these into `contract_type_norm`.
_EMPLOYMENT: dict[str, str] = {
    "fulltime": "Full-time",
    "fulltime_permanent": "Full-time permanent",
    "fulltime_fixed_term": "Full-time fixed term",
    "parttime": "Part-time",
    "parttime_permanent": "Part-time permanent",
    "parttime_fixed_term": "Part-time fixed term",
    "internship": "Internship",
    "freelance": "Freelance",
    "temporary": "Temporary",
    "apprenticeship": "Apprenticeship",
    "volunteer": "Volunteer",
}

_EXPERIENCE: dict[str, str] = {
    "entry_level": "Entry level",
    "junior": "Junior",
    "mid_level": "Mid level",
    "medior": "Mid level",
    "experienced": "Experienced",
    "senior": "Senior",
    "senior_manager": "Senior manager",
    "manager": "Manager",
    "director": "Director",
    "executive": "Executive",
    "student": "Student",
    "intern": "Intern",
}

# Periods that convert to a yearly figure without assuming anything. Hourly and
# daily rates are left out: annualising them needs a working-hours assumption the
# contract has no field to record.
_PERIOD_FACTOR: dict[str, int] = {"year": 1, "yearly": 1, "annual": 1, "month": 12, "week": 52}


class RecruiteeConnector(Connector):
    provider = "recruitee"

    def list_url(self, slug: str) -> str:
        return f"https://{slug}.recruitee.com/api/offers/"

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
        if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
            return self._failed(slug, "unexpected payload shape")

        jobs = [job for job in (self._parse(e, slug) for e in payload["offers"]) if job is not None]
        return self._ok(slug, jobs)

    async def probe(self, client: RateLimitedClient, slug: str) -> ProbeResult:
        try:
            status, payload = await client.get_json(self.list_url(slug))
        except Exception:
            return ProbeResult.ERROR
        if status == 404:
            return ProbeResult.NOT_FOUND
        if status == 200 and isinstance(payload, dict) and isinstance(payload.get("offers"), list):
            return ProbeResult.FOUND
        return ProbeResult.ERROR

    def _parse(self, entry: object, slug: str) -> RawJob | None:
        if not isinstance(entry, dict):
            return None
        job_id = entry.get("id")
        title = entry.get("title")
        if not job_id or not title:
            return None
        if entry.get("status") not in (None, "published"):
            return None

        url = entry.get("careers_url") or entry.get("careers_apply_url")
        if not url:
            offer_slug = entry.get("slug")
            if not offer_slug:
                return None
            url = f"https://{slug}.recruitee.com/o/{offer_slug}"

        work_mode = None
        if entry.get("remote") is True:
            work_mode = "remote"
        elif entry.get("hybrid") is True:
            work_mode = "hybrid"
        elif entry.get("on_site") is True:
            work_mode = "onsite"

        salary_min, salary_max, currency = self._salary(entry.get("salary"))

        return RawJob(
            provider=self.provider,
            external_id=str(job_id),
            title=str(title),
            source_url=str(url),
            description_html=entry.get("description")
            if isinstance(entry.get("description"), str)
            else None,
            requirements_html=entry.get("requirements")
            if isinstance(entry.get("requirements"), str)
            else None,
            location=entry.get("location") if isinstance(entry.get("location"), str) else None,
            city=entry.get("city") if isinstance(entry.get("city"), str) else None,
            country_code=entry.get("country_code")
            if isinstance(entry.get("country_code"), str)
            else None,
            department=entry.get("department")
            if isinstance(entry.get("department"), str)
            else None,
            contract_type=_EMPLOYMENT.get(str(entry.get("employment_type_code") or "")),
            seniority=_EXPERIENCE.get(str(entry.get("experience_code") or "")),
            posted_date=_iso(entry.get("published_at") or entry.get("created_at")),
            work_mode_hint=work_mode,
            salary_min_hint=salary_min,
            salary_max_hint=salary_max,
            salary_currency_hint=currency,
        )

    def _salary(self, raw: object) -> tuple[int | None, int | None, str | None]:
        """Read Recruitee's structured salary, annualising by its stated period."""
        if not isinstance(raw, dict):
            return None, None, None

        currency = str(raw.get("currency") or "").upper()
        if currency not in ALLOWED_CURRENCIES:
            # Out-of-contract currency: report no salary rather than a wrong label.
            return None, None, None

        factor = _PERIOD_FACTOR.get(str(raw.get("period") or "").lower())
        if factor is None:
            return None, None, None

        def value(key: str) -> int | None:
            item = raw.get(key)
            if item in (None, "", 0, "0"):
                return None
            try:
                amount = float(str(item).replace(",", ".")) * factor
            except ValueError:
                return None
            if not (MIN_PLAUSIBLE <= amount <= MAX_PLAUSIBLE):
                return None
            return int(round(amount))

        low, high = value("min"), value("max")
        if low is None and high is None:
            return None, None, None
        return low, high, currency


def _iso(value: object) -> str | None:
    """Recruitee writes `2026-04-21 15:51:44 UTC`; the contract wants ISO 8601."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(" UTC"):
        return text[:-4].replace(" ", "T") + "+00:00"
    return text
