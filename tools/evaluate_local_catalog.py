#!/usr/bin/env python3
"""Compare passes from a locally built catalog with the website's passes.

    python tools/evaluate_local_catalog.py <dir with CelesTrak JSON/CSV dumps>

The directory holds supstarlink, active, rb and deb dumps (JSON or CSV) and
qsmag.zip. Reference passes: tests/fixtures/site_reference_veenendaal.json.
"""

import csv
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import time
import zipfile

from see_a_satellite import group_transits, parse_quicksat, predict_transits, select_satellites
from see_a_satellite.catalog import parse_site_catalog

ROOT = Path(__file__).resolve().parent.parent
START = datetime(2026, 10, 10, 19, tzinfo=UTC)


def load(path: Path) -> list[dict[str, str]]:
    """Read OMM records from a CelesTrak CSV or JSON dump."""
    if path.suffix == ".csv":
        return list(csv.DictReader(path.open()))
    return [{k: str(v) for k, v in e.items()} for e in json.loads(path.read_text())]


def main() -> None:
    """Build the catalog, predict, and compare."""
    src = Path(sys.argv[1])
    records = []
    for name in ("supstarlink", "active", "rb", "deb"):
        path = next(p for p in (src / f"{name}.json", src / f"{name}.csv") if p.exists())
        records += load(path)
    with zipfile.ZipFile(src / "qsmag.zip") as z:
        mags = parse_quicksat(z.read("quicksat.mag").decode("latin-1"))
    sats = select_satellites(records, mags, now=START)
    site = parse_site_catalog(json.loads((ROOT / "tests/fixtures/brightest-sats.json").read_text()))
    site_ids, local_ids = {s.norad_id for s in site}, {s.norad_id for s in sats}
    print(f"local catalog {len(sats)} sats; site {len(site)}; overlap {len(site_ids & local_ids)}")

    began = time.perf_counter()
    groups = group_transits(predict_transits(sats, 52.0277, 5.5583, 194, start=START))
    print(f"predicted {len(groups)} groups in {time.perf_counter() - began:.1f}s")
    ours = [t for g in groups for t in g.transits]
    ref = json.loads((ROOT / "tests/fixtures/site_reference_veenendaal.json").read_text())
    found = missing = 0
    for g in ref:
        for s in g["sats"]:
            start = datetime.fromisoformat(s["start"])
            m = next(
                (
                    t
                    for t in ours
                    if t.satellite.norad_id == s["id"]
                    and abs((t.start - start).total_seconds()) < 120
                ),
                None,
            )
            if m:
                found += 1
                ours.remove(m)
            else:
                missing += 1
                why = "not in local catalog" if s["id"] not in local_ids else "in catalog"
                print(f"  MISSING {s['name']:<24} {start:%a %H:%M} mag {s['mag']:.1f} ({why})")
    print(f"site passes found {found}/{found + missing}; extra local passes {len(ours)}")
    for t in sorted(ours, key=lambda t: t.min_magnitude)[:15]:
        print(
            f"  EXTRA {t.satellite.name:<24} {t.start:%a %H:%M} mag {t.min_magnitude:.1f}"
            f" (site has it: {t.satellite.norad_id in site_ids})"
        )


if __name__ == "__main__":
    main()
