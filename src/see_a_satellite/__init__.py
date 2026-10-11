"""Predict naked-eye satellite passes, as See A Satellite Tonight does.

A Python port of https://james.darpinian.com/satellites/ by James Darpinian.
"""

from .catalog import CatalogError, dump_catalog, fetch_site_catalog, parse_site_catalog
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
from .weather import CloudCover, cloud_cover_at, fetch_cloud_cover

__all__ = [
    "DAYS_TO_CHECK",
    "DEFAULT_MAX_MAGNITUDE",
    "CatalogError",
    "CloudCover",
    "LookAngle",
    "Satellite",
    "Transit",
    "TransitGroup",
    "brightest_possible",
    "cloud_cover_at",
    "dump_catalog",
    "fetch_cloud_cover",
    "fetch_site_catalog",
    "group_transits",
    "parse_site_catalog",
    "predict_transits",
]
