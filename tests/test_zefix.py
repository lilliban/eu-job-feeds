"""Zefix SPARQL client: parsing and pagination.

Verified live against the real LINDAS endpoint while building this (see
`zefix.py`'s module docstring for the numbers) — these tests exercise the
client offline, against canned SPARQL JSON responses shaped like what the
endpoint actually returns.
"""

from __future__ import annotations

import json

from eu_job_feeds.zefix import ZefixCompany, fetch_all, fetch_page, safe_candidate_slugs

from .conftest import FakeClient


def _binding(
    uid: str, name: str, form: str = "https://ld.admin.ch/ech/97/legalforms/0107"
) -> dict:
    return {
        "s": {"type": "uri", "value": "https://register.ld.admin.ch/zefix/company/x"},
        "legalName": {"type": "literal", "value": name},
        "idUri": {
            "type": "uri",
            "value": f"https://register.ld.admin.ch/zefix/company/x/UID/{uid}",
        },
        "form": {"type": "uri", "value": form},
    }


def _page(*bindings: dict) -> str:
    return json.dumps(
        {
            "head": {"vars": ["s", "legalName", "idUri", "form"]},
            "results": {"bindings": list(bindings)},
        }
    )


class TestFetchPage:
    async def test_parses_uid_from_the_identifier_uri(self) -> None:
        """The UID is embedded in a URI (`.../UID/CHE...`), not a bare literal."""
        client = FakeClient(
            {"lindas.admin.ch": (200, _page(_binding("CHE123456789", "Acme GmbH")))}
        )
        page = await fetch_page(client, limit=10, offset=0)
        assert page == [
            ZefixCompany(uid="CHE123456789", legal_name="Acme GmbH", legal_form="GmbH/Sagl")
        ]

    async def test_legal_form_is_labelled(self) -> None:
        client = FakeClient(
            {
                "lindas.admin.ch": (
                    200,
                    _page(
                        _binding(
                            "CHE1", "Acme AG", form="https://ld.admin.ch/ech/97/legalforms/0106"
                        )
                    ),
                )
            }
        )
        page = await fetch_page(client, limit=10, offset=0)
        assert page[0].legal_form == "AG/SA"

    async def test_a_non_200_response_raises(self) -> None:
        client = FakeClient({"lindas.admin.ch": (500, "")})
        raised = False
        try:
            await fetch_page(client, limit=10, offset=0)
        except RuntimeError:
            raised = True
        assert raised


class TestSafeCandidateSlugs:
    """Found live, on the first real batch: `discovery.candidate_slugs`'s bare
    first-word fallback caused 2 false positives in 5 automatic hits. These
    must never reach `zefix-discover`'s unreviewed registry addition."""

    def test_the_bare_first_word_fallback_is_excluded(self) -> None:
        assert "inter" not in safe_candidate_slugs("Inter-Skript AG in Liquidation")

    def test_the_full_name_candidates_are_kept(self) -> None:
        slugs = safe_candidate_slugs("Inter-Skript AG in Liquidation")
        assert slugs == ["interskriptaginliquidation", "inter-skript-ag-in-liquidation"]

    def test_a_single_word_name_is_unaffected(self) -> None:
        assert safe_candidate_slugs("Fujifilm") == ["fujifilm"]


class TestFetchAll:
    async def test_stops_at_a_page_shorter_than_the_page_size(self) -> None:
        """A short page is the last one — no extra request to confirm it."""
        client = FakeClient({"lindas.admin.ch": (200, _page(_binding("CHE1", "Only One")))})
        companies = [c async for c in fetch_all(client, page_size=5)]
        assert [c.uid for c in companies] == ["CHE1"]

    async def test_walks_multiple_full_pages(self) -> None:
        client = FakeClient(
            {
                "lindas.admin.ch": [
                    (200, _page(_binding("CHE1", "First"), _binding("CHE2", "Second"))),
                    (200, _page(_binding("CHE3", "Third"))),
                ]
            }
        )
        companies = [c async for c in fetch_all(client, page_size=2)]
        assert [c.uid for c in companies] == ["CHE1", "CHE2", "CHE3"]

    async def test_an_empty_first_page_yields_nothing(self) -> None:
        client = FakeClient({"lindas.admin.ch": (200, _page())})
        companies = [c async for c in fetch_all(client, page_size=5)]
        assert companies == []
