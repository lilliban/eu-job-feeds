"""HTTP client: per-host rate limiting, retry with backoff.

Rate limiting is per host, not global. Greenhouse and Ashby are unrelated
services; making a request to one wait on the other would multiply the total
runtime by the number of providers for no benefit to anyone.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "eu-job-feeds/0.1 (+https://github.com/lilliban/eu-job-feeds) "
    "public-ATS-feed-collector"
)

# Status codes worth trying again. 408/425/429 are explicit "later" signals;
# 5xx are usually transient. Everything else (401/403/404/422) is a real answer.
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class HostLimiter:
    """Minimum spacing between requests to one host."""

    min_interval: float
    _last: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        async with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class RateLimitedClient:
    """Async HTTP client that spaces requests per host and retries transient errors.

    Personio is deliberately slower than the rest: probing non-existent slugs
    against it returns 429 within a handful of requests (observed while building
    this), and it redirects those to the marketing site rather than failing.
    """

    DEFAULT_INTERVAL = 0.34
    HOST_INTERVALS: dict[str, float] = {
        # Personio throttles the whole `*.jobs.personio.de` space, not per
        # subdomain: a couple of dozen requests at 2s spacing was enough to get
        # every slug 429ed for minutes, including ones that had just worked.
        # Three seconds is the interval that held up in practice.
        "jobs.personio.de": 3.0,
        "api.smartrecruiters.com": 0.5,
        "apply.workable.com": 0.5,
    }

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        max_connections: int = 12,
    ) -> None:
        self._limiters: dict[str, HostLimiter] = {}
        self._limiters_lock = asyncio.Lock()
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=max_connections),
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        )

    async def __aenter__(self) -> "RateLimitedClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _interval_for(self, host: str) -> float:
        for suffix, interval in self.HOST_INTERVALS.items():
            if host == suffix or host.endswith("." + suffix):
                return interval
        return self.DEFAULT_INTERVAL

    async def _limiter(self, host: str) -> HostLimiter:
        # Personio and Recruitee give every company its own subdomain. Limiting by
        # the full hostname would mean no limit at all, so those collapse onto the
        # registrable suffix we care about.
        key = host
        for suffix in self.HOST_INTERVALS:
            if host == suffix or host.endswith("." + suffix):
                key = suffix
                break
        else:
            if host.endswith(".recruitee.com"):
                key = "recruitee.com"
            elif host.endswith(".myworkdayjobs.com"):
                key = host  # each Workday tenant is a separate deployment

        async with self._limiters_lock:
            limiter = self._limiters.get(key)
            if limiter is None:
                limiter = HostLimiter(self._interval_for(host))
                self._limiters[key] = limiter
        return limiter

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        """Perform a request, honouring the host's spacing and retrying transients.

        Raises the last exception if every attempt fails. A response with a
        non-retryable status is returned as-is; deciding what a 404 means belongs
        to the connector, not here.
        """
        host = urlsplit(url).hostname or ""
        limiter = await self._limiter(host)
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            await limiter.acquire()
            try:
                resp = await self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
            except (httpx.TransportError, httpx.InvalidURL) as exc:
                last_exc = exc
                if attempt == self._max_retries:
                    raise
                await self._backoff(attempt, host, repr(exc))
                continue

            if resp.status_code in RETRY_STATUS and attempt < self._max_retries:
                await self._backoff(
                    attempt, host, f"HTTP {resp.status_code}", resp.headers.get("Retry-After")
                )
                continue
            return resp

        assert last_exc is not None
        raise last_exc

    async def _backoff(
        self, attempt: int, host: str, reason: str, retry_after: str | None = None
    ) -> None:
        # Full jitter: several connectors hit the same host in parallel, and a
        # fixed backoff would have them all retry on the same tick.
        delay = min(2.0 * (2**attempt), 30.0)
        delay = random.uniform(0, delay)
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 60.0))
            except ValueError:
                pass
        log.debug("retrying %s in %.1fs (%s)", host, delay, reason)
        await asyncio.sleep(delay)

    async def get_json(self, url: str, **kwargs: object) -> tuple[int, object | None]:
        """GET returning `(status, parsed_json_or_None)`.

        A body that does not parse yields `None` rather than raising: several
        providers answer with an HTML error page under a 200, and the caller needs
        to tell that apart from a real payload.
        """
        resp = await self.request("GET", url, **kwargs)
        try:
            return resp.status_code, resp.json()
        except (ValueError, UnicodeDecodeError):
            return resp.status_code, None

    async def post_json(
        self, url: str, json_body: object, **kwargs: object
    ) -> tuple[int, object | None]:
        resp = await self.request(
            "POST",
            url,
            json=json_body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            **kwargs,
        )
        try:
            return resp.status_code, resp.json()
        except (ValueError, UnicodeDecodeError):
            return resp.status_code, None
