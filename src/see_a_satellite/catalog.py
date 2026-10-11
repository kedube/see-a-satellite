"""Satellite catalogs.

Two sources:

* The catalog published by the See A Satellite Tonight website: CelesTrak
  elements with intrinsic magnitudes and brightness models curated by the
  site's author. One small download.
* A catalog built from public sources: orbital elements from CelesTrak
  (general perturbations for payloads, rocket bodies and debris; SpaceX
  ephemerides for Starlink) and intrinsic magnitudes from Mike McCants'
  Quicksat list. Only objects that can ever reach the magnitude cutoff are
  kept.

CelesTrak asks clients to download each data set at most once per update
(every two hours) and answers repeated requests with a notice instead of
data. :func:`build_catalog` therefore takes the previously built catalog and
keeps its elements for anything it could not refresh.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import csv
from datetime import UTC, datetime, timedelta
import io
import json
import logging
import math
import statistics
from typing import Any
import zipfile

import aiohttp

from .predict import DEFAULT_MAX_MAGNITUDE, Satellite, brightest_possible

_LOGGER = logging.getLogger(__name__)

SITE_CATALOG_BASE_URL = "https://pub-ac416a81df124ba7997caa2baca8bc2c.r2.dev/"
SITE_CATALOGS = {
    "brightest": "brightest-sats.json",
    "starlink": "starlink-sats.json",
}
QUICKSAT_URL = "https://www.mmccants.org/programs/qsmag.zip"
CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
CELESTRAK_SUPGP_URL = "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php"

# Supplemental Starlink elements first: they are fitted to SpaceX's own
# ephemerides and are more accurate than the radar-based ones.
CELESTRAK_QUERIES: tuple[tuple[str, Mapping[str, str]], ...] = (
    (CELESTRAK_SUPGP_URL, {"FILE": "starlink"}),
    (CELESTRAK_GP_URL, {"GROUP": "active"}),
    (CELESTRAK_GP_URL, {"NAME": "R/B"}),
    (CELESTRAK_GP_URL, {"NAME": "DEB"}),
)

# Quicksat magnitudes are for a fully illuminated object at 1000 km; the
# site's catalog uses the half-illuminated convention, which is 0.75 fainter
# (the site's catalog matches Quicksat + 0.7 or + 0.8 for most objects).
QUICKSAT_OFFSET = 0.75
# Typical Starlink brightness used by the site's catalog, and the altitude
# below which Starlinks are still raising their orbit (brighter, own model).
STARLINK_MAGNITUDE = 5.1
STARLINK_RAISING_BELOW_KM = 357.0
# Elements older than this are too inaccurate to predict passes.
MAX_ELEMENT_AGE = timedelta(days=30)

_TIMEOUT = aiohttp.ClientTimeout(total=120)
DEFAULT_USER_AGENT = "see-a-satellite (+https://pypi.org/project/see-a-satellite/)"


class CatalogError(Exception):
    """A catalog could not be downloaded or parsed."""


class RateLimitedError(CatalogError):
    """CelesTrak declined the request because the data has not changed."""


# --- Parsing ------------------------------------------------------------------


def parse_site_catalog(entries: Iterable[Mapping[str, Any]]) -> list[Satellite]:
    """Parse the website's JSON catalog (OMM fields plus MAG and MODEL)."""
    satellites = []
    for entry in entries:
        fields = {
            k: v for k, v in entry.items() if k not in ("MAG", "MODEL", "TLE_LINE1", "TLE_LINE2")
        }
        satellites.append(Satellite.from_omm(fields, entry.get("MAG"), entry.get("MODEL")))
    return satellites


def parse_quicksat(text: str) -> dict[int, float]:
    """Intrinsic magnitudes from McCants' quicksat.mag, in the site's convention.

    Fixed columns: catalog number 0-5, magnitude 33-37. Objects without a
    magnitude are left out.
    """
    magnitudes = {}
    for line in text.splitlines()[1:]:
        try:
            norad_id = int(line[0:5])
            magnitudes[norad_id] = float(line[33:37]) + QUICKSAT_OFFSET
        except ValueError:
            continue
    return magnitudes


def parse_omm_csv(text: str) -> list[dict[str, str]]:
    """Parse CelesTrak OMM CSV."""
    if not text.startswith("OBJECT_NAME"):
        if "limit requests" in text or "not updated" in text:
            raise RateLimitedError(text.strip().splitlines()[0])
        raise CatalogError(f"Unexpected CelesTrak response: {text[:100]!r}")
    return list(csv.DictReader(io.StringIO(text)))


def select_satellites(
    records: Iterable[Mapping[str, str]],
    magnitudes: Mapping[int, float],
    *,
    max_magnitude: float = DEFAULT_MAX_MAGNITUDE,
    now: datetime | None = None,
) -> list[Satellite]:
    """Satellites from OMM records that can reach the magnitude cutoff.

    The first record of a catalog number wins. Starlinks get the site's
    typical magnitude, or its orbit-raising model while they are low. Rocket
    bodies and debris without a measured magnitude get the median of
    identically named objects (the same stage or tank), as the site does.
    """
    now = now or datetime.now(UTC)
    unique: dict[int, Mapping[str, str]] = {}
    for record in records:
        unique.setdefault(int(record["NORAD_CAT_ID"]), record)
    typical = _typical_magnitudes(unique, magnitudes)
    selected = []
    for norad_id, record in unique.items():
        name = record["OBJECT_NAME"]
        if name.upper().startswith("STARLINK"):
            sat = Satellite.from_omm(record, STARLINK_MAGNITUDE)
            if "DTC" in name.upper():
                sat.model = "starlink-dtc"
            elif sat.perigee_km < STARLINK_RAISING_BELOW_KM:
                sat.model = "starlink-raising"
        elif (magnitude := magnitudes.get(norad_id, typical.get(name))) is not None:
            sat = Satellite.from_omm(record, magnitude)
        else:
            continue
        if now - sat.epoch > MAX_ELEMENT_AGE:
            continue
        if brightest_possible(sat) < max_magnitude:
            selected.append(sat)
    return selected


def _typical_magnitudes(
    records: Mapping[int, Mapping[str, str]], magnitudes: Mapping[int, float]
) -> dict[str, float]:
    """Median magnitude per rocket body / debris name, e.g. "H-2A R/B"."""
    by_name: dict[str, list[float]] = {}
    for norad_id, record in records.items():
        name = record["OBJECT_NAME"]
        if (" R/B" in name or " DEB" in name) and norad_id in magnitudes:
            by_name.setdefault(name, []).append(magnitudes[norad_id])
    return {name: statistics.median(mags) for name, mags in by_name.items()}


# --- Downloading ----------------------------------------------------------------


async def _get(
    session: aiohttp.ClientSession,
    url: str,
    params: Mapping[str, str] | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> bytes:
    try:
        async with session.get(
            url, params=params, timeout=_TIMEOUT, headers={"User-Agent": user_agent}
        ) as resp:
            resp.raise_for_status()
            return await resp.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CatalogError(f"Error fetching {url}: {err}") from err


async def fetch_site_catalog(
    session: aiohttp.ClientSession,
    name: str = "brightest",
    *,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[Satellite]:
    """Download one of the website's catalogs ("brightest" or "starlink")."""
    data = await _get(session, SITE_CATALOG_BASE_URL + SITE_CATALOGS[name], user_agent=user_agent)
    try:
        entries = json.loads(data)
        satellites = parse_site_catalog(entries)
    except (ValueError, KeyError, TypeError) as err:
        raise CatalogError(f"Malformed site catalog: {err}") from err
    if not satellites:
        raise CatalogError("Site catalog is empty")
    return satellites


async def fetch_quicksat(
    session: aiohttp.ClientSession, *, user_agent: str = DEFAULT_USER_AGENT
) -> dict[int, float]:
    """Download McCants' intrinsic magnitudes."""
    data = await _get(session, QUICKSAT_URL, user_agent=user_agent)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            text = archive.read("quicksat.mag").decode("latin-1")
    except (zipfile.BadZipFile, KeyError) as err:
        raise CatalogError(f"Malformed Quicksat archive: {err}") from err
    magnitudes = parse_quicksat(text)
    if not magnitudes:
        raise CatalogError("Quicksat magnitude list is empty")
    return magnitudes


async def build_catalog(
    session: aiohttp.ClientSession,
    *,
    max_magnitude: float = DEFAULT_MAX_MAGNITUDE,
    magnitudes: Mapping[int, float] | None = None,
    previous: Sequence[Satellite] = (),
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[Satellite]:
    """Build a catalog from CelesTrak elements and Quicksat magnitudes.

    Pass ``magnitudes`` to reuse a Quicksat list fetched earlier (it changes
    rarely), and ``previous`` to keep elements for objects whose data set
    could not be refreshed. Raises :class:`CatalogError` if nothing usable
    is available.
    """
    if magnitudes is None:
        magnitudes = await fetch_quicksat(session, user_agent=user_agent)
    records: list[dict[str, str]] = []
    failures = 0
    for url, params in CELESTRAK_QUERIES:
        try:
            text = (await _get(session, url, {**params, "FORMAT": "csv"}, user_agent)).decode()
            records.extend(parse_omm_csv(text))
        except CatalogError as err:
            failures += 1
            _LOGGER.debug("Keeping previous elements for %s: %s", params, err)
    if failures == len(CELESTRAK_QUERIES) and not previous:
        raise CatalogError("No orbital elements could be downloaded")
    satellites = select_satellites(records, magnitudes, max_magnitude=max_magnitude)
    fetched = {int(r["NORAD_CAT_ID"]) for r in records}
    now = datetime.now(UTC)
    satellites.extend(
        sat
        for sat in previous
        if sat.norad_id not in fetched
        and now - sat.epoch <= MAX_ELEMENT_AGE
        and brightest_possible(sat) < max_magnitude
    )
    if not satellites:
        raise CatalogError("No satellites can reach the magnitude cutoff")
    return satellites


def dump_catalog(satellites: Iterable[Satellite]) -> list[dict[str, Any]]:
    """Serialize a catalog to JSON-compatible dicts in the site's format."""
    out = []
    for sat in satellites:
        entry: dict[str, Any] = dict(sat.omm)
        entry["OBJECT_NAME"] = sat.name
        if sat.magnitude is not None and math.isfinite(sat.magnitude):
            entry["MAG"] = sat.magnitude
        if sat.model:
            entry["MODEL"] = sat.model
        out.append(entry)
    return out
