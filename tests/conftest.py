"""Test helpers."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL


class FakeResponse:
    def __init__(self, url: str, body: bytes, status: int) -> None:
        self.url, self.body, self.status = url, body, status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            info = aiohttp.RequestInfo(
                URL(self.url), "GET", CIMultiDictProxy(CIMultiDict()), URL(self.url)
            )
            raise aiohttp.ClientResponseError(info, (), status=self.status)

    async def read(self) -> bytes:
        return self.body

    async def json(self) -> Any:
        return json.loads(self.body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeSession:
    """Stands in for aiohttp.ClientSession: maps URL + query to a response."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, Mapping[str, str], Any]] = []
        self.requests: list[tuple[str, dict[str, str]]] = []

    def add(self, url: str, response: Any, **params: str) -> None:
        """Respond to GET url (with at least these params) with bytes/str/status/exception."""
        self.routes.insert(0, (url, params, response))

    def get(self, url: str, params: Mapping[str, str] | None = None, **kwargs: Any) -> FakeResponse:
        params = dict(params or {})
        self.requests.append((url, params))
        for route_url, route_params, response in self.routes:
            if route_url == url and all(params.get(k) == v for k, v in route_params.items()):
                if isinstance(response, BaseException):
                    raise response
                if isinstance(response, int):
                    return FakeResponse(url, b"", response)
                body = response.encode() if isinstance(response, str) else response
                return FakeResponse(url, body, 200)
        return FakeResponse(url, b"", 404)
