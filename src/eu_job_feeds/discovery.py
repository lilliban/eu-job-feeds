"""Find which board a company is on, and remember the misses.

Knowing that a company has *no* public ATS board is as useful as knowing it has
one: without that memory, every run spends seven requests re-learning it. But a
permanent record is worse than none — a company that adopts Greenhouse next
month would never be looked at again. Hence a negative cache with a **30-day
expiry**.

`ProbeResult.ERROR` is never cached. A timeout or a 429 means the question was
not answered, and recording it as "absent" would turn a transient outage into a
month of silence. Personio in particular 429s readily.

`ProbeResult.AMBIGUOUS` is not cached as a miss either: SmartRecruiters cannot
distinguish an unknown company from an idle one, so its slugs need a human.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .connectors import CONNECTORS, ProbeResult
from .http import RateLimitedClient
from .models import utcnow_iso

log = logging.getLogger(__name__)

NEGATIVE_PATH = Path("registry/negative.json")
#: How long a "this company is not on this board" answer stays trusted.
NEGATIVE_TTL_DAYS = 30


@dataclass(frozen=True)
class DiscoveryHit:
    provider: str
    slug: str
    result: ProbeResult


def _still_valid(expires_at: str, now: datetime) -> bool:
    """True while `now` is before the recorded expiry.

    A malformed timestamp counts as expired: re-probing costs one request,
    trusting a value we cannot read costs a month of not looking.
    """
    try:
        deadline = datetime.fromisoformat(expires_at)
    except (ValueError, TypeError):
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now < deadline


class NegativeCache:
    """Slugs known to be absent from a provider, with an expiry on each."""

    def __init__(self, entries: dict[str, str] | None = None) -> None:
        # key -> ISO timestamp of when the answer stops being trusted
        self._entries: dict[str, str] = entries or {}

    @staticmethod
    def _key(provider: str, slug: str) -> str:
        return f"{provider}:{slug}"

    def is_known_absent(self, provider: str, slug: str, now: datetime | None = None) -> bool:
        expires = self._entries.get(self._key(provider, slug))
        if expires is None:
            return False
        return _still_valid(expires, now or datetime.now(timezone.utc))

    def remember_absent(self, provider: str, slug: str, now: datetime | None = None) -> None:
        moment = now or datetime.now(timezone.utc)
        deadline = (moment + timedelta(days=NEGATIVE_TTL_DAYS)).replace(microsecond=0)
        self._entries[self._key(provider, slug)] = deadline.isoformat()

    def forget(self, provider: str, slug: str) -> None:
        self._entries.pop(self._key(provider, slug), None)

    def purge_expired(self, now: datetime | None = None) -> int:
        """Drop entries past their expiry so the file cannot grow without bound."""
        moment = now or datetime.now(timezone.utc)
        stale = [
            key
            for key, expires in self._entries.items()
            if not _still_valid(expires, moment)
        ]
        for key in stale:
            del self._entries[key]
        return len(stale)

    def to_dict(self) -> dict:
        return {"expires_at": dict(sorted(self._entries.items()))}


def load_negative_cache(path: Path = NEGATIVE_PATH) -> NegativeCache:
    if not path.exists():
        return NegativeCache()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("negative cache at %s is unreadable; starting empty", path)
        return NegativeCache()
    entries = raw.get("expires_at") if isinstance(raw, dict) else None
    return NegativeCache(entries if isinstance(entries, dict) else None)


def save_negative_cache(cache: NegativeCache, path: Path = NEGATIVE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def candidate_slugs(company: str) -> list[str]:
    """Plausible slugs for a company name, most likely first.

    Kept short on purpose. Most guesses miss — `netflix`, `catawiki`, `bynder`,
    `mollie` and `picnic` were all dead when checked — so a long candidate list
    mostly buys wasted requests against providers that are already rate-limited.
    """
    base = company.strip().lower()
    if not base:
        return []
    import re

    cleaned = re.sub(r"[^a-z0-9\s-]", "", base)
    cleaned = re.sub(r"\s+(?:inc|llc|ltd|limited|gmbh|ag|sa|srl|spa|bv|nv|oy|ab|as)\.?$", "", cleaned)
    words = [w for w in re.split(r"[\s-]+", cleaned) if w]
    if not words:
        return []

    joined = "".join(words)
    out = [joined]
    if len(words) > 1:
        out.append("-".join(words))
    if joined != words[0]:
        out.append(words[0])
    seen: set[str] = set()
    return [s for s in out if s and not (s in seen or seen.add(s))]


async def discover(
    client: RateLimitedClient,
    company: str,
    *,
    cache: NegativeCache,
    slugs: list[str] | None = None,
    providers: list[str] | None = None,
) -> list[DiscoveryHit]:
    """Probe a company's candidate slugs across providers.

    Returns every hit found — a company can genuinely be on two boards — plus any
    AMBIGUOUS answers, which a human has to confirm.
    """
    names = slugs or candidate_slugs(company)
    chosen = providers or list(CONNECTORS)
    hits: list[DiscoveryHit] = []

    for provider in chosen:
        connector = CONNECTORS.get(provider)
        if connector is None:
            log.warning("unknown provider %r, skipping", provider)
            continue
        for slug in names:
            if cache.is_known_absent(provider, slug):
                log.debug("skipping %s/%s: known absent and not yet expired", provider, slug)
                continue

            result = await connector.probe(client, slug)
            if result is ProbeResult.FOUND:
                cache.forget(provider, slug)
                hits.append(DiscoveryHit(provider, slug, result))
                break  # this company is on this board; stop trying its other slugs
            if result is ProbeResult.NOT_FOUND:
                cache.remember_absent(provider, slug)
            elif result is ProbeResult.AMBIGUOUS:
                # Not a miss and not a hit. Surfaced so a person can decide,
                # never written to the negative cache.
                hits.append(DiscoveryHit(provider, slug, result))
            # ERROR: nothing was learned, so nothing is recorded.

    return hits


def _significant_words(name: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9\s]", " ", name.lower()).split() if w}


def names_plausibly_match(expected: str, actual: str) -> bool:
    """Whether a provider-reported display name is consistent with the
    company a slug was guessed for.

    Asymmetric on purpose: a provider's own name is often a shortened form of
    the legal name ("Aleph" for "Aleph Alpha"), so every word of `actual` must
    appear in `expected` — not the other way round. A word in `actual` that
    `expected` does not have ("CUBE Edu Services Pte Ltd" has "edu",
    "services", "pte", "ltd" that "Cube Dev" does not) is the signal that the
    slug landed on an unrelated company — confirmed on real data: of 10
    `source="discovered"` entries re-checked a week after being added, this
    rule would have caught both confirmed mismatches (see docs/DECISIONS.md
    #19, second addendum).
    """
    actual_words = _significant_words(actual)
    expected_words = _significant_words(expected)
    if not actual_words or not expected_words:
        return True  # nothing to compare; absence of data is not a mismatch
    return actual_words <= expected_words


async def verify_name(
    client: RateLimitedClient, provider: str, slug: str, expected_name: str
) -> bool | None:
    """Fetch the board and check its reported name against `expected_name`.

    Returns `None` — not a verdict — when the provider carries no name at all
    (Ashby, Lever: `Connector.account_name` returns `None` unconditionally)
    or the fetch itself fails. Callers must treat `None` as *unverifiable*,
    never as confirmation. Costs one extra request, only for a slug already
    confirmed `FOUND`, so this never runs against the bulk of misses.
    """
    connector = CONNECTORS.get(provider)
    if connector is None:
        return None
    try:
        status, payload = await client.get_json(connector.list_url(slug))
    except Exception:
        return None
    if status != 200:
        return None
    actual = connector.account_name(payload)
    if actual is None:
        return None
    return names_plausibly_match(expected_name, actual)
