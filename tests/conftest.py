"""Shared fixtures and a fake HTTP client.

The connector tests run offline against payloads captured from the real APIs
(`tests/fixtures/`). Inventing payloads would only ever test the parser against
the shape I imagined; these are the shapes the providers actually send.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json_fixture(name: str) -> object:
    return json.loads(load_fixture(name))


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self) -> object:
        return json.loads(self.text)


class FakeClient:
    """Stands in for `RateLimitedClient`, serving canned responses by URL.

    `routes` maps a URL substring to `(status, body)`. Unmatched URLs raise, so a
    connector that builds an unexpected URL fails loudly instead of silently
    returning nothing.
    """

    def __init__(self, routes: dict[str, tuple[int, str]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def _match(self, url: str) -> tuple[int, str]:
        self.calls.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected URL requested: {url}")

    async def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        status, body = self._match(url)
        return FakeResponse(status, body)

    async def get_json(self, url: str, **kwargs: object) -> tuple[int, object | None]:
        status, body = self._match(url)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, None

    async def post_json(
        self, url: str, json_body: object, **kwargs: object
    ) -> tuple[int, object | None]:
        status, body = self._match(url)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, None


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
