"""Lever job boards.

`https://api.lever.co/v0/postings/{slug}?mode=json`

Verified against `spotify` (105 postings) and an invented slug (clean 404 JSON).
`plaid` answers `200 []` — a live account with no open roles, which is a
different thing from a missing one and must not be cached as "not found".

The payload is a JSON array at the root, not an object.
"""

from __future__ import annotations

from ..http import RateLimitedClient
from ..models import FetchOutcome, RawJob
from .base import Connector, ProbeResult


class LeverConnector(Connector):
    provider = "lever"

    BASE = "https://api.lever.co/v0/postings"

    def list_url(self, slug: str) -> str:
        return f"{self.BASE}/{slug}?mode=json"

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
        if not isinstance(payload, list):
            return self._failed(slug, "expected a JSON array at the root")

        jobs = [job for job in (self._parse(e) for e in payload) if job is not None]
        return self._ok(slug, jobs)

    async def probe(self, client: RateLimitedClient, slug: str) -> ProbeResult:
        try:
            status, payload = await client.get_json(self.list_url(slug))
        except Exception:
            return ProbeResult.ERROR
        if status == 404:
            return ProbeResult.NOT_FOUND
        if status == 200 and isinstance(payload, list):
            return ProbeResult.FOUND
        return ProbeResult.ERROR

    def _parse(self, entry: object) -> RawJob | None:
        if not isinstance(entry, dict):
            return None
        job_id = entry.get("id")
        title = entry.get("text")
        url = entry.get("hostedUrl") or entry.get("applyUrl")
        if not job_id or not title or not url:
            return None

        categories = entry.get("categories")
        categories = categories if isinstance(categories, dict) else {}

        # Lever splits the advert across several fields: `description` is the
        # intro, `lists` holds the bulleted sections — which is where the
        # requirements actually live — and `additional` the closing boilerplate.
        # The HTML variants are used throughout, because `lists[].content` is HTML
        # regardless and a body half plain, half markup would confuse the splitter.
        # The section headings are wrapped in <h3> so they survive as their own
        # line, which is what `split_description_requirements` matches on.
        body_parts: list[str] = []
        intro = entry.get("description")
        if isinstance(intro, str) and intro.strip():
            body_parts.append(intro)

        lists = entry.get("lists")
        if isinstance(lists, list):
            for section in lists:
                if not isinstance(section, dict):
                    continue
                heading = section.get("text")
                content = section.get("content")
                if isinstance(heading, str) and heading.strip():
                    body_parts.append(f"<h3>{heading.strip()}</h3>")
                if isinstance(content, str) and content.strip():
                    body_parts.append(f"<ul>{content}</ul>")

        closing = entry.get("additional")
        if isinstance(closing, str) and closing.strip():
            body_parts.append(closing)

        body = "\n".join(body_parts).strip() or None

        # `createdAt` is epoch milliseconds.
        posted = None
        created = entry.get("createdAt")
        if isinstance(created, (int, float)) and created > 0:
            from datetime import datetime, timezone

            posted = (
                datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
            )

        location = categories.get("location") or entry.get("workplaceType")
        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list) and len(all_locations) > 1:
            location = ", ".join(str(x) for x in all_locations if x)

        return RawJob(
            provider=self.provider,
            external_id=str(job_id),
            title=str(title),
            source_url=str(url),
            description_html=body,
            location=location if isinstance(location, str) else None,
            country_code=entry.get("country") if isinstance(entry.get("country"), str) else None,
            department=categories.get("department") or categories.get("team"),
            contract_type=categories.get("commitment"),
            posted_date=posted,
            work_mode_hint=_WORKPLACE.get(str(entry.get("workplaceType", "")).lower()),
        )


# Lever's `workplaceType` is a closed vocabulary, so it is a mapping and not a
# regex. `unspecified` is deliberately absent: it means the company did not say.
_WORKPLACE: dict[str, str] = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
    "on-site": "onsite",
}
