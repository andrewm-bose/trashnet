"""Denver Power Outage Monitor - Main entry point.

Checks Xcel Energy's outage map for significant outages in the Denver metro
area and sends notifications via email and ntfy.

Usage:
    python -m outage_monitor.main          # normal run
    python -m outage_monitor.main --test   # send a test notification
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from .config import (
    ArcGISConfig,
    GroceryConfig,
    NotificationConfig,
    NtfyConfig,
    OutageCriteria,
)
from .grocery_finder import find_groceries_for_outages
from .notifier import send_notifications
from .outage_filter import filter_outages
from .xcel_client import Outage, XcelOutageClient


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _make_test_outage() -> Outage:
    """Create a fake outage for testing the notification pipeline."""
    return Outage(
        id="TEST-001",
        latitude=39.7392,
        longitude=-104.9903,
        customers_affected=12345,
        start_time=datetime.now(timezone.utc) - timedelta(hours=3),
        etr=datetime.now(timezone.utc) + timedelta(hours=2),
        cause="TEST - Verifying notification delivery",
        crew_status="Test Mode",
        city="Denver",
        zip_code="80202",
        state="CO",
        source_data={"test": True},
    )


def main() -> int:
    setup_logging()
    logger = logging.getLogger(__name__)

    test_mode = "--test" in sys.argv

    if test_mode:
        logger.info("Running in TEST MODE - sending a fake outage notification")
        matching = [_make_test_outage()]
    else:
        logger.info("Starting Denver Power Outage Monitor")

        # Fetch outages from Xcel via ArcGIS MapServer
        arcgis_config = ArcGISConfig()
        client = XcelOutageClient(arcgis_config)
        all_outages = client.fetch_outages(state="CO")
        logger.info("Fetched %d total outages from Xcel Energy", len(all_outages))

        if not all_outages:
            logger.info("No outages reported. All clear!")
            return 0

        # Filter to Denver area + criteria
        criteria = OutageCriteria()
        matching = filter_outages(all_outages, criteria)

        if not matching:
            logger.info(
                "No outages in Denver area meeting criteria "
                "(>= %d customers, >= %.1f hours)",
                criteria.min_customers_affected,
                criteria.min_duration_hours,
            )
            return 0

    logger.info(
        "%d outage(s) match criteria - preparing notifications", len(matching)
    )

    # Find nearby grocery stores (if enabled)
    grocery_config = GroceryConfig()
    grocery_data = find_groceries_for_outages(matching, grocery_config)

    # Send notifications
    notification_config = NotificationConfig()
    ntfy_config = NtfyConfig()
    sent = send_notifications(matching, notification_config, ntfy_config, grocery_data)

    if sent:
        logger.info("Notifications sent successfully")
    else:
        logger.warning("Failed to send notifications (output printed to console)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
