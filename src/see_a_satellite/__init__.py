"""Predict naked-eye satellite passes, as See A Satellite Tonight does.

A Python port of https://james.darpinian.com/satellites/ by James Darpinian.
"""

from .catalog import (
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
from .predict import (
    DAYS_TO_CHECK,
    DEFAULT_MAX_MAGNITUDE,
    LookAngle,
    Satellite,
    Transit,
    TransitGroup,
    brightest_possible,
    group_transits,
    predict_transits,
)

__all__ = [
    "DAYS_TO_CHECK",
    "DEFAULT_MAX_MAGNITUDE",
    "CatalogError",
    "LookAngle",
    "RateLimitedError",
    "Satellite",
    "Transit",
    "TransitGroup",
    "brightest_possible",
    "build_catalog",
    "dump_catalog",
    "fetch_quicksat",
    "fetch_site_catalog",
    "group_transits",
    "parse_omm_csv",
    "parse_quicksat",
    "parse_site_catalog",
    "predict_transits",
    "select_satellites",
]
