"""Tests for the cloud cover forecast."""

from datetime import UTC, datetime
import json

import pytest

from see_a_satellite import CatalogError, cloud_cover_at, fetch_cloud_cover
from see_a_satellite.weather import OPEN_METEO_URL

from .conftest import FakeSession

START = int(datetime(2026, 10, 10, tzinfo=UTC).timestamp())


async def test_fetch_cloud_cover() -> None:
    session = FakeSession()
    session.add(
        OPEN_METEO_URL,
        json.dumps({"hourly": {"time": [START, START + 3600], "cloud_cover": [10, None]}}),
    )
    forecast = await fetch_cloud_cover(session, 52.0277, 5.5583)  # type: ignore[arg-type]
    assert forecast == [(datetime(2026, 10, 10, tzinfo=UTC), 10)]
    assert session.requests[0][1]["latitude"] == "52.0277"


async def test_fetch_cloud_cover_errors() -> None:
    session = FakeSession()
    session.add(OPEN_METEO_URL, 500)
    with pytest.raises(CatalogError):
        await fetch_cloud_cover(session, 0, 0)  # type: ignore[arg-type]
    session.add(OPEN_METEO_URL, '{"unexpected": 1}')
    with pytest.raises(CatalogError):
        await fetch_cloud_cover(session, 0, 0)  # type: ignore[arg-type]


def test_cloud_cover_at() -> None:
    forecast = [(datetime.fromtimestamp(START + 3600 * i, UTC), i) for i in range(3)]
    assert cloud_cover_at(forecast, datetime(2026, 10, 10, 0, 30, tzinfo=UTC)) == 1
    assert cloud_cover_at(forecast, datetime(2026, 10, 10, 5, tzinfo=UTC)) is None
    assert cloud_cover_at(None, datetime(2026, 10, 10, tzinfo=UTC)) is None
