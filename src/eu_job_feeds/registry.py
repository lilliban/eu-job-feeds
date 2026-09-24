"""Company registry: the authority on which company is on which board.

**The canonical company name lives here and nowhere else.** A connector knows
`datadog`; the user searching the dataset types `Datadog`. Deriving the name
from the slug produces `Datadog` for `datadog` but `Boschgroup` for
`BoschGroup` and `Amazingcarecareers` for `amazingcarecareers` — postings filed
under a name nobody will ever type are postings nobody will ever find.

A provider's own display name (Greenhouse's `company_name`, Workable's `name`)
is used only to *propose* a new entry during discovery. It never overwrites a
curated one.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .connectors.workday import WorkdayTarget
from .models import utcnow_iso

log = logging.getLogger(__name__)

REGISTRY_PATH = Path("registry/companies.yaml")


class WorkdayIdentity(BaseModel):
    """Workday's three-part identity. None of the parts is derivable."""

    model_config = ConfigDict(extra="forbid")

    tenant: str
    wd: str
    site: str

    def to_target(self) -> WorkdayTarget:
        return WorkdayTarget(tenant=self.tenant, wd=self.wd, site=self.site)


class CompanyEntry(BaseModel):
    """One company on one board."""

    model_config = ConfigDict(extra="forbid")

    #: Canonical, human-facing name. Written into every posting's `company_name`.
    name: str
    provider: str
    #: Opaque provider identifier. Case is significant — SmartRecruiters returns
    #: an empty list rather than an error for `bosch` when the id is `BoschGroup`.
    slug: str | None = None
    workday: WorkdayIdentity | None = None
    #: `curated` entries are never overwritten by discovery.
    source: str = "curated"
    added_at: str = Field(default_factory=utcnow_iso)
    #: Set to False to keep an entry on record without querying it.
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("company name must not be blank")
        return v.strip()

    @property
    def key(self) -> str:
        """Stable identity of this entry, and the dataset filename stem."""
        if self.provider == "workday" and self.workday:
            return self.workday.tenant
        return self.slug or ""

    def model_post_init(self, _context: object) -> None:
        if self.provider == "workday":
            if self.workday is None:
                raise ValueError(f"{self.name}: workday entries need tenant/wd/site")
        elif not self.slug:
            raise ValueError(f"{self.name}: {self.provider} entries need a slug")


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[CompanyEntry] = Field(default_factory=list)

    def enabled(self) -> Iterator[CompanyEntry]:
        return (c for c in self.companies if c.enabled)

    def find(self, provider: str, key: str) -> CompanyEntry | None:
        for entry in self.companies:
            if entry.provider == provider and entry.key == key:
                return entry
        return None

    def name_for(self, provider: str, key: str, fallback: str | None = None) -> str:
        """Canonical name for a board, or `fallback` when the board is unknown."""
        entry = self.find(provider, key)
        if entry:
            return entry.name
        # Deliberately not `key.title()`: a guessed name is how postings become
        # unfindable. The caller supplies what the provider itself reported.
        return fallback or key

    def upsert(self, entry: CompanyEntry) -> bool:
        """Add an entry, or fill in gaps on a discovered one. Returns True if changed.

        A curated entry is left untouched: a human decided that name.
        """
        existing = self.find(entry.provider, entry.key)
        if existing is None:
            self.companies.append(entry)
            return True
        if existing.source == "curated":
            return False
        changed = False
        if existing.name != entry.name and entry.source == "curated":
            existing.name = entry.name
            changed = True
        return changed

    def sorted(self) -> list[CompanyEntry]:
        return sorted(self.companies, key=lambda c: (c.provider, c.key.lower()))


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    if not path.exists():
        return Registry()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return Registry.model_validate(raw)


def save_registry(registry: Registry, path: Path = REGISTRY_PATH) -> None:
    """Write the registry back, sorted, so a diff shows only real changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "companies": [
            {k: v for k, v in entry.model_dump(mode="json").items() if v is not None}
            for entry in registry.sorted()
        ]
    }
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
