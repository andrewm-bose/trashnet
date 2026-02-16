"""Find grocery stores near power outage areas using Google Places API."""

import logging

import requests

from .config import GroceryConfig
from .kubra_client import Outage

logger = logging.getLogger(__name__)


def find_nearby_groceries(
    outage: Outage,
    config: GroceryConfig | None = None,
) -> str:
    """Find grocery stores near an outage location.

    Returns a formatted string listing nearby grocery stores, or empty string
    if the feature is disabled or no results found.
    """
    config = config or GroceryConfig()

    if not config.enabled:
        return ""

    if not config.google_api_key:
        logger.warning("Grocery search enabled but GOOGLE_PLACES_API_KEY not set")
        return ""

    try:
        url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        params = {
            "location": f"{outage.latitude},{outage.longitude}",
            "radius": config.search_radius_meters,
            "type": "grocery_or_supermarket",
            "key": config.google_api_key,
        }

        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "OK":
            logger.warning("Places API returned status: %s", data.get("status"))
            return ""

        results = data.get("results", [])[:config.max_results]
        if not results:
            return "  No grocery stores found within search radius."

        lines = []
        for place in results:
            name = place.get("name", "Unknown")
            address = place.get("vicinity", "Address unknown")
            is_open = place.get("opening_hours", {}).get("open_now")
            status = ""
            if is_open is True:
                status = " (OPEN)"
            elif is_open is False:
                status = " (CLOSED)"
            lines.append(f"  - {name}{status}")
            lines.append(f"    {address}")

        return "\n".join(lines)

    except requests.RequestException as e:
        logger.error("Failed to search for grocery stores: %s", e)
        return ""


def find_groceries_for_outages(
    outages: list[Outage],
    config: GroceryConfig | None = None,
) -> dict[str, str]:
    """Find grocery stores for all outages. Returns {outage_id: grocery_info}."""
    config = config or GroceryConfig()
    results = {}

    if not config.enabled:
        return results

    for outage in outages:
        info = find_nearby_groceries(outage, config)
        if info:
            results[outage.id] = info

    return results
