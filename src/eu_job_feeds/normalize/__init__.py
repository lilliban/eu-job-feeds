"""Turn a connector's `RawJob` into a contract-shaped `JobPosting`.

Normalisation follows the cost order in the brief: a value the ATS already
provides as structured data is used as-is, regex over the text is the fallback,
and a field that neither yields stays None.
"""

from __future__ import annotations

from ..models import JobPosting, RawJob, seen_stamp
from ..text import (
    clean_text,
    content_hash,
    html_to_text,
    split_description_requirements,
)
from .classify import (
    infer_seniority,
    infer_work_mode,
    normalize_contract_type,
    normalize_work_mode,
)
from .experience import extract_years, parse_structured_range
from .languages import extract_languages
from .location import normalize_country_code, split_location
from .salary import extract_salary

__all__ = [
    "build_posting",
    "extract_languages",
    "extract_salary",
    "extract_years",
    "infer_seniority",
    "infer_work_mode",
    "normalize_contract_type",
    "normalize_country_code",
    "normalize_work_mode",
    "parse_structured_range",
    "split_location",
]


def build_posting(
    raw: RawJob,
    *,
    company_name: str,
    seen_at: str | None = None,
) -> JobPosting:
    """Normalise one posting.

    `company_name` is passed in rather than read off the payload: the canonical
    name is a registry decision, not something a connector may infer from a slug
    (trap 2). Providers that happen to return a display name are used only to
    seed the registry, never to overwrite it here.
    """
    now = seen_at or seen_stamp()

    description = raw.description_text or html_to_text(raw.description_html)
    requirements = raw.requirements_text or html_to_text(raw.requirements_html)

    # Greenhouse, Ashby, Lever and Workday ship one undivided body. Splitting it
    # at the requirements heading is what makes `requirements_raw` populated at
    # all, and the content hash is defined over requirements — so a provider that
    # separates the two fields itself (Recruitee) is left alone.
    if requirements is None and description:
        description, requirements = split_description_requirements(description)

    # With no heading to split on there is nothing to hash but the body. An empty
    # third component would collapse every same-titled role of a company into one
    # hash, which is worse than hashing more text than the contract describes.
    requirements_for_hash = requirements or description

    haystack = "\n".join(p for p in (raw.title, description, requirements) if p)

    work_mode = raw.work_mode_hint or normalize_work_mode(raw.location) or infer_work_mode(
        raw.location, raw.title, description, requirements
    )

    min_years, max_years = raw.min_years_exp_hint, raw.max_years_exp_hint
    if min_years is None and max_years is None:
        min_years, max_years = extract_years(haystack)

    salary_min, salary_max, currency = (
        raw.salary_min_hint,
        raw.salary_max_hint,
        raw.salary_currency_hint,
    )
    if salary_min is None and salary_max is None:
        salary_min, salary_max, currency = extract_salary(haystack)
    if currency is None:
        salary_min = salary_max = None

    city, country = raw.city, normalize_country_code(raw.country_code)
    if city is None or country is None:
        derived_city, derived_country = split_location(raw.location)
        city = city or derived_city
        country = country or derived_country

    return JobPosting(
        content_hash=content_hash(raw.title, company_name, requirements_for_hash),
        title=clean_text(raw.title) or raw.title,
        company_name=company_name,
        source_url=raw.source_url,
        source_board=raw.provider,
        source_kind="ats",
        external_id=raw.external_id,
        description=description,
        requirements_raw=requirements,
        location=clean_text(raw.location),
        contract_type=clean_text(raw.contract_type),
        contract_type_norm=normalize_contract_type(raw.contract_type)
        or normalize_contract_type(raw.title),
        work_mode=work_mode,
        seniority=clean_text(raw.seniority) or infer_seniority(raw.title),
        department=clean_text(raw.department),
        min_years_exp=min_years,
        max_years_exp=max_years,
        languages=extract_languages(requirements, description),
        city=clean_text(city),
        country_code=country,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=currency,
        posted_date=raw.posted_date,
        first_seen_at=now,
        last_seen_at=now,
        consecutive_misses=0,
        is_closed=False,
    )
