"""Tests for downloading, parsing and building satellite catalogs."""

import csv
from datetime import UTC, datetime, timedelta
import io
import json
from pathlib import Path
import zipfile

import pytest

from see_a_satellite import (
    CatalogError,
    RateLimitedError,
    build_catalog,
    dump_catalog,
    fetch_quicksat,
    fetch_site_catalog,
    parse_omm_csv,
    parse_quicksat,
    parse_site_catalog,
    select_satellites,
)
from see_a_satellite.catalog import (
    CELESTRAK_GP_URL,
    CELESTRAK_SUPGP_URL,
    QUICKSAT_URL,
    SITE_CATALOG_BASE_URL,
)

from .conftest import FakeSession

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 10, 19, tzinfo=UTC)
OMM_FIELDS = [
    "OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
    "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "EPHEMERIS_TYPE",
    "CLASSIFICATION_TYPE", "NORAD_CAT_ID", "ELEMENT_SET_NO", "REV_AT_EPOCH", "BSTAR",
    "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT",
]  # fmt: skip
RATE_LIMITED = (
    "GP data has not updated since your last successful download of GROUP=active "
    "at 2026-10-11 00:41:02 UTC\nPlease limit requests to once every 2 hours\n"
)


@pytest.fixture(scope="module")
def site_entries() -> list[dict]:
    return json.loads((FIXTURES / "brightest-sats.json").read_text())


def _record(entry: dict, **changes) -> dict[str, str]:
    record = {k: str(entry.get(k, "0")) for k in OMM_FIELDS}
    record.update({k: str(v) for k, v in changes.items()})
    return record


def _csv(records: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, OMM_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(records)
    return out.getvalue()


def _by_id(entries: list[dict], norad_id: int) -> dict:
    return next(e for e in entries if e["NORAD_CAT_ID"] == norad_id)


def test_parse_quicksat() -> None:
    """Quicksat magnitudes are converted to the site's convention."""
    magnitudes = parse_quicksat((FIXTURES / "quicksat.mag").read_text("latin-1"))
    assert magnitudes[25544] == pytest.approx(-2.5 + 0.75)
    assert magnitudes[5] == pytest.approx(9.5 + 0.75)  # Vanguard 1
    assert 53807 not in magnitudes  # BlueWalker 3 is listed without magnitude


def test_parse_omm_csv(site_entries) -> None:
    records = parse_omm_csv(_csv([_record(site_entries[0])]))
    assert records[0]["NORAD_CAT_ID"] == "694"
    with pytest.raises(RateLimitedError):
        parse_omm_csv(RATE_LIMITED)
    with pytest.raises(CatalogError):
        parse_omm_csv("<html>oops</html>")


def test_select_satellites(site_entries) -> None:
    iss = _record(_by_id(site_entries, 25544))
    faint = _record(_by_id(site_entries, 694))  # ATLAS CENTAUR 2 R/B, eccentric
    stale = _record(_by_id(site_entries, 25994), EPOCH="2026-08-01T00:00:00")
    unmeasured_stage = _record(_by_id(site_entries, 33500))  # H-2A R/B
    measured_stage = _record(_by_id(site_entries, 27601))  # H-2A R/B
    raising = _record(
        _by_id(site_entries, 25544), OBJECT_NAME="STARLINK-99999", NORAD_CAT_ID="99999",
        MEAN_MOTION="16.05",
    )  # fmt: skip
    on_station = _record(
        _by_id(site_entries, 25544), OBJECT_NAME="STARLINK-99998", NORAD_CAT_ID="99998",
        MEAN_MOTION="15.06",
    )  # fmt: skip
    duplicate_iss = _record(_by_id(site_entries, 25544), OBJECT_NAME="ISS DUPLICATE")
    sats = select_satellites(
        [iss, faint, stale, unmeasured_stage, measured_stage, raising, on_station, duplicate_iss],
        {25544: -1.75, 694: 4.5, 25994: 2.75, 27601: 2.8},
        now=NOW,
    )
    by_id = {s.norad_id: s for s in sats}
    assert by_id[25544].name == "ISS"  # first record wins
    assert 694 not in by_id  # can never get bright enough
    assert 25994 not in by_id  # elements too old
    assert by_id[33500].magnitude == 2.8  # typical for its stage
    assert by_id[99999].model == "starlink-raising"
    assert 99998 not in by_id  # on-station Starlinks are too faint at mag 3


def test_dump_catalog_round_trip(site_entries) -> None:
    sats = parse_site_catalog(site_entries[:20])
    again = parse_site_catalog(json.loads(json.dumps(dump_catalog(sats))))
    assert [(s.norad_id, s.name, s.magnitude, s.model, s.epoch) for s in again] == [
        (s.norad_id, s.name, s.magnitude, s.model, s.epoch) for s in sats
    ]


async def test_fetch_site_catalog(site_entries) -> None:
    session = FakeSession()
    session.add(SITE_CATALOG_BASE_URL + "brightest-sats.json", json.dumps(site_entries))
    session.add(SITE_CATALOG_BASE_URL + "starlink-sats.json", 503)
    assert len(await fetch_site_catalog(session)) == 864  # type: ignore[arg-type]
    with pytest.raises(CatalogError):
        await fetch_site_catalog(session, "starlink")  # type: ignore[arg-type]


async def test_fetch_quicksat() -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("quicksat.mag", (FIXTURES / "quicksat.mag").read_bytes())
    session = FakeSession()
    session.add(QUICKSAT_URL, archive.getvalue())
    assert (await fetch_quicksat(session))[25544] == pytest.approx(-1.75)  # type: ignore[arg-type]
    session.add(QUICKSAT_URL, b"not a zip")
    with pytest.raises(CatalogError):
        await fetch_quicksat(session)  # type: ignore[arg-type]


def _celestrak(body_by_query: dict[str, str]) -> FakeSession:
    session = FakeSession()
    for query, body in body_by_query.items():
        key, value = query.split("=", 1)
        base = CELESTRAK_SUPGP_URL if key == "FILE" else CELESTRAK_GP_URL
        session.add(base, body, **{key: value})
    return session


def _fresh() -> str:
    return datetime.now(UTC).isoformat()[:26]


async def test_build_catalog(site_entries) -> None:
    iss = _record(_by_id(site_entries, 25544), EPOCH=_fresh())
    stage = _record(_by_id(site_entries, 27601), EPOCH=_fresh())
    session = _celestrak(
        {
            "FILE=starlink": _csv([]),
            "GROUP=active": _csv([iss]),
            "NAME=R/B": _csv([stage]),
            "NAME=DEB": _csv([]),
        }
    )
    sats = await build_catalog(session, magnitudes={25544: -1.75, 27601: 2.8})  # type: ignore[arg-type]
    assert {s.norad_id for s in sats} == {25544, 27601}
    assert all(params["FORMAT"] == "csv" for _, params in session.requests)


async def test_build_catalog_keeps_previous_when_rate_limited(site_entries) -> None:
    iss = _record(_by_id(site_entries, 25544), EPOCH=_fresh())
    previous = select_satellites(
        [_record(_by_id(site_entries, 27601), EPOCH=_fresh())], {27601: 2.8}
    )
    session = _celestrak(
        {
            "FILE=starlink": RATE_LIMITED,
            "GROUP=active": _csv([iss]),
            "NAME=R/B": RATE_LIMITED,
            "NAME=DEB": RATE_LIMITED,
        }
    )
    sats = await build_catalog(session, magnitudes={25544: -1.75}, previous=previous)  # type: ignore[arg-type]
    assert {s.norad_id for s in sats} == {25544, 27601}


async def test_build_catalog_without_any_data_fails() -> None:
    session = _celestrak(
        {q: RATE_LIMITED for q in ("FILE=starlink", "GROUP=active", "NAME=R/B", "NAME=DEB")}
    )
    with pytest.raises(CatalogError):
        await build_catalog(session, magnitudes={25544: -1.75})  # type: ignore[arg-type]


async def test_build_catalog_expires_previous_elements(site_entries) -> None:
    """Elements kept from an earlier build are dropped once they are too old."""
    long_ago = datetime.now(UTC) - timedelta(days=40)
    stale = select_satellites(
        [_record(_by_id(site_entries, 27601), EPOCH=long_ago.isoformat()[:26])],
        {27601: 2.8},
        now=long_ago,
    )
    assert stale
    iss = _record(_by_id(site_entries, 25544), EPOCH=_fresh())
    session = _celestrak(
        {
            "FILE=starlink": RATE_LIMITED,
            "GROUP=active": _csv([iss]),
            "NAME=R/B": RATE_LIMITED,
            "NAME=DEB": RATE_LIMITED,
        }
    )
    sats = await build_catalog(session, magnitudes={25544: -1.75}, previous=stale)  # type: ignore[arg-type]
    assert {s.norad_id for s in sats} == {25544}
