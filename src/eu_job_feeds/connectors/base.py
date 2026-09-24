"""The shape every ATS connector implements.

Two operations, deliberately separate:

* `fetch` reads a company's whole catalogue. Its `FetchOutcome.complete` flag is
  what the lifecycle trusts, so a connector must set it to False whenever it did
  not manage to walk the entire feed.
* `probe` answers "does this slug exist on this provider?" for discovery. It has
  a third answer, `AMBIGUOUS`, because some providers cannot tell an unknown
  company apart from a company with no open roles.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob


class ProbeResult(enum.Enum):
    """Outcome of a discovery probe."""

    FOUND = "found"
    NOT_FOUND = "not_found"
    #: The provider answered, but its answer cannot distinguish "no such company"
    #: from "company with zero open positions" (SmartRecruiters, Workable).
    AMBIGUOUS = "ambiguous"
    #: The request itself failed. Never cached: nothing was learned.
    ERROR = "error"


class Connector(ABC):
    """Base class for a single-endpoint ATS."""

    #: Value written to `source_board` in the dataset.
    provider: str

    @abstractmethod
    def list_url(self, slug: str) -> str:
        """URL of the company's full posting list."""

    @abstractmethod
    async def fetch(
        self,
        client: RateLimitedClient,
        slug: str,
        *,
        already_detailed: frozenset[str] = frozenset(),
    ) -> FetchOutcome:
        """Read the company's entire catalogue.

        `already_detailed` holds the external ids whose advert text is already in
        the archive. Providers whose list endpoint carries no description
        (SmartRecruiters, Workday) need one extra request per posting, and
        Bosch's 4713 openings would be 4713 requests on every run. Skipping the
        ones already stored turns a steady-state run into a handful of calls.

        It must never affect `complete`: the list walk decides which postings
        exist, and a posting whose text was skipped is still present.
        """

    async def probe(self, client: RateLimitedClient, slug: str) -> ProbeResult:
        """Default probe: a successful fetch with at least one posting proves it.

        Providers with a cheaper or more reliable existence check override this.
        """
        outcome = await self.fetch(client, slug)
        if outcome.error:
            return ProbeResult.ERROR
        if outcome.jobs:
            return ProbeResult.FOUND
        return ProbeResult.AMBIGUOUS

    # -- helpers shared by subclasses -------------------------------------

    def _ok(self, slug: str, jobs: list[RawJob]) -> FetchOutcome:
        return FetchOutcome(provider=self.provider, slug=slug, complete=True, jobs=jobs)

    def _empty(self, slug: str) -> FetchOutcome:
        """A confirmed-empty catalogue. Complete: the company exists, has no roles."""
        return FetchOutcome(provider=self.provider, slug=slug, complete=True, jobs=[])

    def _failed(self, slug: str, error: str) -> FetchOutcome:
        """A read that did not complete. The lifecycle must ignore this run."""
        return FetchOutcome(provider=self.provider, slug=slug, complete=False, error=error)
