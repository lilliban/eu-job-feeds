"""Pydantic models for the published dataset.

The field names in `JobPosting` are the contract with the consuming application.
Adding fields is safe; renaming or removing them is not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ContractTypeNorm = Literal["full_time", "part_time", "contract", "internship"]
WorkMode = Literal["remote", "hybrid", "onsite"]

# Order of keys as written to disk. Stable ordering keeps git diffs minimal:
# a field that did not change must serialise to the same bytes on every run.
FIELD_ORDER: tuple[str, ...] = (
    "content_hash",
    "title",
    "company_name",
    "source_url",
    "source_board",
    "source_kind",
    "external_id",
    "description",
    "requirements_raw",
    "location",
    "contract_type",
    "contract_type_norm",
    "work_mode",
    "seniority",
    "department",
    "min_years_exp",
    "max_years_exp",
    "languages",
    "city",
    "country_code",
    "salary_min",
    "salary_max",
    "salary_currency",
    "posted_date",
    "first_seen_at",
    "last_seen_at",
    "consecutive_misses",
    "is_closed",
)


def utcnow_iso() -> str:
    """Current time as a second-precision ISO 8601 UTC string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def seen_stamp(now: datetime | None = None) -> str:
    """The timestamp used for `first_seen_at` and `last_seen_at`, truncated to the day.

    `last_seen_at` is rewritten for every posting on every successful read. At
    full precision and four runs a day, every company file would change four
    times a day even when no posting did — which is exactly the churn that
    one-file-per-company exists to avoid, and it would bury the real changes in
    a diff nobody can read.

    Truncating to midnight UTC keeps the field honest (it still says which read
    last saw the posting, to the day) while making an unchanged company file
    byte-identical between runs on the same day. It stays a full ISO 8601
    datetime, so a consumer parsing it as one is unaffected.
    """
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()


class JobPosting(BaseModel):
    """One job advert as published in `data/{provider}/{slug}.json`."""

    model_config = ConfigDict(extra="forbid")

    content_hash: str
    title: str
    company_name: str
    source_url: str
    source_board: str
    source_kind: str = "ats"
    #: The provider's own id for this advert. Added on top of the agreed schema
    #: (the contract allows new fields) because the lifecycle needs a key that is
    #: one-to-one with an advert across runs: `content_hash` is shared by the same
    #: role in several cities, and `source_url` is not what connectors return.
    #: Defaults to empty so a file written before this field existed still loads.
    external_id: str = ""
    description: str | None = None
    requirements_raw: str | None = None
    location: str | None = None

    contract_type: str | None = None
    contract_type_norm: ContractTypeNorm | None = None
    work_mode: WorkMode | None = None
    seniority: str | None = None
    department: str | None = None
    min_years_exp: int | None = None
    max_years_exp: int | None = None
    languages: list[str] = Field(default_factory=list)
    city: str | None = None
    country_code: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_date: str | None = None

    first_seen_at: str
    last_seen_at: str
    consecutive_misses: int = 0
    is_closed: bool = False

    @field_validator("country_code")
    @classmethod
    def _upper_country(cls, v: str | None) -> str | None:
        return v.upper() if v else None

    def to_ordered_dict(self) -> dict:
        """Serialise with `FIELD_ORDER` first, then any newer field alphabetically.

        New fields added to the model later still get written, but they land in a
        deterministic place instead of wherever pydantic happens to put them.
        """
        raw = self.model_dump(mode="json")
        out: dict = {}
        for key in FIELD_ORDER:
            if key in raw:
                out[key] = raw.pop(key)
        for key in sorted(raw):
            out[key] = raw[key]
        return out


class RawJob(BaseModel):
    """A posting as a connector extracted it, before normalisation.

    Connectors fill only what the provider actually gives them. Everything the
    connector cannot know (canonical company name, derived fields) is left out and
    resolved further down the pipeline.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    external_id: str
    title: str
    source_url: str
    description_html: str | None = None
    description_text: str | None = None
    requirements_html: str | None = None
    requirements_text: str | None = None
    location: str | None = None
    city: str | None = None
    country_code: str | None = None
    department: str | None = None
    contract_type: str | None = None
    seniority: str | None = None
    posted_date: str | None = None

    # Structured hints the ATS already provides. Preferred over regex when set,
    # per the normalisation cost order: provider JSON > regex > nothing.
    work_mode_hint: WorkMode | None = None
    min_years_exp_hint: int | None = None
    max_years_exp_hint: int | None = None
    salary_min_hint: int | None = None
    salary_max_hint: int | None = None
    salary_currency_hint: str | None = None


class FetchOutcome(BaseModel):
    """Result of reading one company's feed.

    `complete` is the field the lifecycle depends on. It means: this response is
    the provider's whole catalogue for this company, so a posting that is absent
    from it is genuinely absent. A partial page walk, a timeout, a 5xx or a parse
    failure must set it to False, otherwise the lifecycle would close postings
    that were merely not looked at (trap 4).
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    slug: str
    complete: bool
    jobs: list[RawJob] = Field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.complete and self.error is None
