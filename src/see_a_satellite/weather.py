"""Cloud cover forecast, as the website shows for each pass."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from datetime import UTC, datetime

import aiohttp

from .catalog import DEFAULT_USER_AGENT, CatalogError

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = aiohttp.ClientTimeout(total=30)

type CloudCover = Sequence[tuple[datetime, int]]


async def fetch_cloud_cover(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
) -> CloudCover:
    """Hourly cloud cover percentages for the next 7 days from Open-Meteo."""
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "hourly": "cloud_cover",
        "forecast_days": "7",
        "timeformat": "unixtime",
    }
    try:
        async with session.get(
            OPEN_METEO_URL, params=params, timeout=_TIMEOUT, headers={"User-Agent": user_agent}
        ) as resp:
            resp.raise_for_status()
            hourly = (await resp.json())["hourly"]
        return [
            (datetime.fromtimestamp(t, UTC), int(c))
            for t, c in zip(hourly["time"], hourly["cloud_cover"], strict=False)
            if c is not None
        ]
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as err:
        raise CatalogError(f"Error fetching cloud cover: {err}") from err


def cloud_cover_at(forecast: CloudCover | None, when: datetime) -> int | None:
    """Cloud cover of the first forecast hour after a time, as the site shows it."""
    if not forecast:
        return None
    i = bisect_right([t for t, _ in forecast], when)
    return forecast[i][1] if i < len(forecast) else None
