"""Find grocery stores near power outage areas using Google Places API."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .config import GroceryConfig
from .xcel_client import Outage

logger = logging.getLogger(__name__)

_DENVER_TZ = ZoneInfo("America/Denver")

# Google Places opening_hours uses 0=Sunday, 1=Monday, ..., 6=Saturday.
# Python's weekday() uses 0=Monday, ..., 6=Sunday.
# This maps Python weekday → Google day number.
_PY_WEEKDAY_TO_GOOGLE = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}


def _get_closing_time(place_id: str, api_key: str) -> str:
    """Fetch today's closing time for a place via the Place Details API.

    Returns a string like "closes 9:00 PM" or "" if unavailable.
    """
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields": "opening_hours",
                "key": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "OK":
            return ""

        periods = (
            data.get("result", {})
            .get("opening_hours", {})
            .get("periods", [])
        )
        if not periods:
            return ""

        # A single period with only "open" and day=0, time=0000 means 24 hours
        if len(periods) == 1 and "close" not in periods[0]:
            return "24 hours"

        now_denver = datetime.now(_DENVER_TZ)
        google_day = _PY_WEEKDAY_TO_GOOGLE[now_denver.weekday()]

        for period in periods:
            if period.get("open", {}).get("day") == google_day:
                close = period.get("close", {})
                close_time = close.get("time", "")
                if close_time and len(close_time) == 4:
                    hour = int(close_time[:2])
                    minute = int(close_time[2:])
                    ampm = "AM" if hour < 12 else "PM"
                    display_hour = hour % 12 or 12
                    if minute:
                        return f"closes {display_hour}:{minute:02d} {ampm}"
                    return f"closes {display_hour} {ampm}"

    except (requests.RequestException, ValueError, KeyError) as e:
        logger.debug("Failed to fetch closing time for %s: %s", place_id, e)

    return ""


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
            place_id = place.get("place_id", "")
            is_open = place.get("opening_hours", {}).get("open_now")

            # Build status string: "OPEN, closes 9 PM" or "CLOSED"
            if is_open is True:
                closing = _get_closing_time(place_id, config.google_api_key) if place_id else ""
                status = f" (OPEN, {closing})" if closing else " (OPEN)"
            elif is_open is False:
                status = " (CLOSED)"
            else:
                status = ""

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
