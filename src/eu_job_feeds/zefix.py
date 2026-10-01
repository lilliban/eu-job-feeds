"""Client for the Zefix company register, via the LINDAS SPARQL endpoint.

Zefix (the Swiss central commercial register) is queryable without a key or
account on `https://lindas.admin.ch/query`, against the graph
`https://lindas.admin.ch/foj/zefix`, updated daily. (There is also a
PublicREST API on `zefix.admin.ch`, but it requires requesting an account by
email — skipped, LINDAS is enough and keyless.)

Verified live against the real endpoint while building this: the graph holds
794,210 active legal entities. Restricting to the eCH-0097 legal forms AG/SA
(`0106`) and GmbH/Sagl (`0107`) — the only forms treated as "a real company"
here, since sole proprietorships, associations and foundations almost never
run a structured ATS — narrows that to 543,190. `schema:identifier` is not
one-to-one with a company: most entities carry two or three identifier URIs
(UID, CHID, a legacy register number), so a query that joins on it without
filtering to the UID-shaped one triples every row (measured: 1,629,570 vs
543,190 distinct). `FILTER(CONTAINS(STR(?idUri), "/UID/"))` is what keeps the
join one-to-one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from .discovery import candidate_slugs
from .http import RateLimitedClient

ENDPOINT = "https://lindas.admin.ch/query"
GRAPH = "https://lindas.admin.ch/foj/zefix"

#: eCH-0097 legal form codes -> short label. See module docstring for why only
#: these two are used.
LEGAL_FORMS: dict[str, str] = {
    "https://ld.admin.ch/ech/97/legalforms/0106": "AG/SA",
    "https://ld.admin.ch/ech/97/legalforms/0107": "GmbH/Sagl",
}

#: A weekly full pull is ~543,190 rows; at 5000/page that is ~109 requests,
#: acceptable for a job that runs once a week, not something to shrink further.
DEFAULT_PAGE_SIZE = 5000


@dataclass(frozen=True)
class ZefixCompany:
    uid: str
    legal_name: str
    legal_form: str


def _query(limit: int, offset: int) -> str:
    forms = ", ".join(f"<{form}>" for form in LEGAL_FORMS)
    return f"""
PREFIX schema: <http://schema.org/>
SELECT ?s ?legalName ?idUri ?form WHERE {{
  GRAPH <{GRAPH}> {{
    ?s a <https://schema.ld.admin.ch/ZefixOrganisation> ;
       schema:legalName ?legalName ;
       schema:identifier ?idUri ;
       schema:additionalType ?form .
    FILTER(?form IN ({forms}))
    FILTER(CONTAINS(STR(?idUri), "/UID/"))
  }}
}}
ORDER BY ?s
LIMIT {limit} OFFSET {offset}
"""


def _parse_row(row: dict) -> ZefixCompany:
    id_uri = row["idUri"]["value"]
    uid = id_uri.rsplit("/UID/", 1)[-1]
    form_uri = row["form"]["value"]
    return ZefixCompany(
        uid=uid,
        legal_name=row["legalName"]["value"],
        legal_form=LEGAL_FORMS.get(form_uri, form_uri),
    )


async def fetch_page(
    client: RateLimitedClient, *, limit: int, offset: int
) -> list[ZefixCompany]:
    status, data = await client.get_json(
        ENDPOINT,
        params={"query": _query(limit, offset)},
        headers={"Accept": "application/sparql-results+json"},
    )
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"LINDAS query failed: HTTP {status}")
    return [_parse_row(row) for row in data["results"]["bindings"]]


async def fetch_all(
    client: RateLimitedClient, *, page_size: int = DEFAULT_PAGE_SIZE
) -> AsyncIterator[ZefixCompany]:
    """Every active AG/SA and GmbH/Sagl, paginated.

    `ORDER BY ?s` in the query gives a stable order across pages — without it,
    `OFFSET` against a graph that can change between requests would risk
    skipping or repeating rows.
    """
    offset = 0
    while True:
        page = await fetch_page(client, limit=page_size, offset=offset)
        if not page:
            return
        for company in page:
            yield company
        if len(page) < page_size:
            return
        offset += page_size


def safe_candidate_slugs(legal_name: str) -> list[str]:
    """Candidate slugs trusted enough for *unreviewed* registry addition.

    `discovery.candidate_slugs` includes a bare-first-word fallback for a
    multi-word name (e.g. "inter" for "Inter-Skript AG in Liquidation") — a
    reasonable guess when a person reviews the hit before it is added
    (`eu-job-feeds discover`), but not when it goes straight into the registry
    unreviewed (`zefix-discover`, docs/DECISIONS.md #19). Measured on the
    first real batch: 2 of 5 automatic hits were false positives, both via
    this fallback — "inter" matched an unrelated Brazilian company's
    Greenhouse board, "art" an unrelated German company's Personio board.
    Dropping it also loses true positives whose real slug happens to be just
    the first word (e.g. "ALSO Holding AG" -> "also") — accepted, since a
    collision on an entire company name is far rarer than on one common word
    of it.
    """
    return candidate_slugs(legal_name)[:2]
