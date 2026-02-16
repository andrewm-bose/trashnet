"""Filter outages based on configurable criteria."""

import logging

from .config import DenverArea, OutageCriteria
from .kubra_client import Outage

logger = logging.getLogger(__name__)


def is_in_denver_area(outage: Outage, bounds: DenverArea | None = None) -> bool:
    """Check if an outage falls within the Denver metro area bounding box."""
    bounds = bounds or DenverArea()
    return (
        bounds.min_lat <= outage.latitude <= bounds.max_lat
        and bounds.min_lon <= outage.longitude <= bounds.max_lon
    )


def meets_criteria(outage: Outage, criteria: OutageCriteria | None = None) -> bool:
    """Check if an outage meets the notification criteria."""
    criteria = criteria or OutageCriteria()

    if outage.customers_affected < criteria.min_customers_affected:
        return False

    if outage.duration_hours is not None:
        if outage.duration_hours < criteria.min_duration_hours:
            return False

    return True


def filter_outages(
    outages: list[Outage],
    criteria: OutageCriteria | None = None,
    bounds: DenverArea | None = None,
) -> list[Outage]:
    """Filter outages to only those in Denver that meet notification criteria."""
    criteria = criteria or OutageCriteria()
    bounds = bounds or DenverArea()

    results = []
    for outage in outages:
        if not is_in_denver_area(outage, bounds):
            logger.debug(
                "Outage %s outside Denver area: %s",
                outage.id, outage.area_description,
            )
            continue

        if not meets_criteria(outage, criteria):
            logger.debug(
                "Outage %s doesn't meet criteria: %d customers, %.1fh duration",
                outage.id,
                outage.customers_affected,
                outage.duration_hours or 0,
            )
            continue

        results.append(outage)

    logger.info(
        "Filtered %d outages down to %d matching criteria in Denver area",
        len(outages), len(results),
    )
    return results
