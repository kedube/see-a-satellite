# see-a-satellite

Predict when satellites are visible to the naked eye, the way
[See A Satellite Tonight](https://james.darpinian.com/satellites/) does. This
is a Python port of James Darpinian's site, made with his permission. It powers
the Home Assistant *See A Satellite Tonight* integration.

```python
import aiohttp
from see_a_satellite import fetch_site_catalog, group_transits, predict_transits

async with aiohttp.ClientSession() as session:
    satellites = await fetch_site_catalog(session)

# CPU bound (a few seconds): run it in an executor from async code.
for group in group_transits(predict_transits(satellites, 52.03, 5.56, 10)):
    t = group.primary
    print(group.start, t.satellite.name, f"mag {group.min_magnitude:.1f}",
          f"rises {t.rise.azimuth:.0f}°/{t.rise.elevation:.0f}°", "bright" if group.bright else "")
```

## What it computes

The pipeline follows the website step by step:

- **Propagation.** SGP4 runs from OMM elements at one-minute steps for 5 days, and positions are rotated from the TEME frame to Earth-fixed coordinates.
- **Brightness.** The intrinsic magnitude at 1000 km is adjusted for range, atmospheric extinction (Green 1992) and phase angle (diffuse sphere). Starlink and BlueBird satellites use their own phase-function models (Mallama et al. 2025; Cole et al. 2025).
- **Visibility.** A satellite counts as visible when it is at least 10° up, brighter than the limiting magnitude for the sun's depression (Weaver 1947), and lit by the sun above the troposphere (grazing height ≥ 15 km).
- **Refinement and grouping.** Each pass is refined to one second. Overlapping passes are grouped, incidental Starlinks are pruned, and a "bright" label is applied.

Checked against the website, for every pass the site shows over 5 days:

- start times agree within 9 s
- end times agree within 19 s
- magnitudes agree within 0.03

## Catalog

`fetch_site_catalog(session, "brightest" | "starlink")` downloads the
website's catalog, which the site's author collects and curates. Orbital
elements come mostly from [Space-Track](https://www.space-track.org/) (US
Space Force 18th Space Defense Squadron), and Starlink elements come from
[CelesTrak](https://celestrak.org/). Magnitudes come from Mike McCants and the
MMT-9 survey (Karpov et al. 2016), and constellation phase functions come from
published photometry. See James's
[write-up](https://james.darpinian.com/blog/how-see-a-satellite-tonight-works).
This library only consumes the catalog. The site refreshes it every 8 hours.
Cache it (`dump_catalog` / `parse_site_catalog`) rather than downloading it on
every prediction.

## Development

```sh
uv sync
uv run pytest && uv run mypy && uv run ruff check src tests
```
