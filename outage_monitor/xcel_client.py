"""Client for fetching outage data from Xcel Energy via ArcGIS MapServer.

Xcel Energy's outage map is powered by Esri ArcGIS.  We query the MapServer
REST endpoint directly for current outage records.

The outage map is at: https://www.outagemap-xcelenergy.com/outagemap/
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .config import ArcGISConfig

logger = logging.getLogger(__name__)


@dataclass
class Outage:
    """Represents a single power outage."""

    id: str
    latitude: float
    longitude: float
    customers_affected: int
    start_time: datetime | None
    etr: datetime | None  # estimated time of restoration
    cause: str
    crew_status: str
    city: str
    zip_code: str
    state: str
    source_data: dict  # raw data for debugging

    @property
    def duration_hours(self) -> float | None:
        if self.start_time is None:
            return None
        delta = datetime.now(timezone.utc) - self.start_time
        return delta.total_seconds() / 3600

    @property
    def area_description(self) -> str:
        parts = [p for p in [self.city, self.state, self.zip_code] if p]
        if parts:
            return ", ".join(parts)
        return f"({self.latitude:.4f}, {self.longitude:.4f})"


# Fields we request from the ArcGIS endpoint.
_OUT_FIELDS = ",".join([
    "objectid",
    "globalid",
    "ticket",
    "cause",
    "city",
    "county",
    "crewstatus",
    "customers",
    "etr",
    "latlong",
    "off_",
    "outagestatus",
    "outagetype",
    "states",
    "xcelarea",
    "comments",
])


class XcelOutageClient:
    """Fetches outage data from Xcel Energy via ArcGIS MapServer."""

    # ArcGIS servers typically cap results per request.
    _PAGE_SIZE = 1000

    def __init__(self, config: ArcGISConfig | None = None):
        self.config = config or ArcGISConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.outagemap-xcelenergy.com/",
            "Accept": "application/json",
        })

    # -- public API ----------------------------------------------------------

    def fetch_outages(self, state: str = "CO") -> list[Outage]:
        """Fetch all current outages, optionally filtered by state."""
        try:
            raw_features = self._query_all_features(state)
            logger.info("Fetched %d raw features from ArcGIS", len(raw_features))

            outages: list[Outage] = []
            for feature in raw_features:
                outage = self._parse_feature(feature)
                if outage:
                    outages.append(outage)

            logger.info("Parsed %d outages with valid data", len(outages))
            return outages

        except requests.RequestException as e:
            logger.error("Failed to fetch outage data from ArcGIS: %s", e)
            return []

    # -- ArcGIS query --------------------------------------------------------

    def _query_all_features(self, state: str = "CO") -> list[dict]:
        """Query the ArcGIS endpoint with pagination."""
        all_features: list[dict] = []
        offset = 0

        while True:
            params = {
                "f": "json",
                "where": f"states = '{state}'" if state else "1=1",
                "outFields": _OUT_FIELDS,
                "returnGeometry": "true",
                "outSR": "4326",  # WGS84 lat/lon
                "resultOffset": str(offset),
                "resultRecordCount": str(self._PAGE_SIZE),
            }

            url = f"{self.config.base_url}/query"
            logger.info(
                "Querying ArcGIS (offset=%d, state=%s)", offset, state or "all",
            )
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.error("ArcGIS query error: %s", data["error"])
                break

            features = data.get("features", [])
            if not features:
                break

            all_features.extend(features)
            logger.info("  ... got %d features (total so far: %d)",
                        len(features), len(all_features))

            # If we got fewer than the page size, we've reached the end.
            if len(features) < self._PAGE_SIZE:
                break

            offset += len(features)

        return all_features

    # -- parsing -------------------------------------------------------------

    def _parse_feature(self, feature: dict) -> Outage | None:
        """Parse an ArcGIS feature into our Outage dataclass."""
        try:
            attrs = feature.get("attributes", {})
            geom = feature.get("geometry", {})

            # Coordinates: prefer geometry point, fall back to latlong field
            lat = geom.get("y")
            lon = geom.get("x")

            if lat is None or lon is None:
                lat, lon = self._parse_latlong_field(attrs.get("latlong", ""))

            if lat is None or lon is None:
                return None

            customers = int(attrs.get("customers", 0) or 0)

            start_time = self._parse_timestamp(attrs.get("off_"))
            etr = self._parse_timestamp(attrs.get("etr"))
            cause = str(attrs.get("cause", "Unknown") or "Unknown")
            crew_status = str(attrs.get("crewstatus", "Unknown") or "Unknown")

            city = str(attrs.get("city", "") or "")
            state_val = str(attrs.get("states", "") or "")

            outage_id = str(
                attrs.get("ticket")
                or attrs.get("globalid")
                or attrs.get("objectid")
                or f"{lat}_{lon}"
            )

            return Outage(
                id=outage_id,
                latitude=lat,
                longitude=lon,
                customers_affected=customers,
                start_time=start_time,
                etr=etr,
                cause=cause,
                crew_status=crew_status,
                city=city,
                zip_code="",  # not in the ArcGIS fields
                state=state_val,
                source_data=attrs,
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug("Failed to parse ArcGIS feature: %s - data: %s", e, feature)
            return None

    @staticmethod
    def _parse_latlong_field(value: str) -> tuple[float | None, float | None]:
        """Parse the 'latlong' text field (format varies: 'lat,lon' or similar)."""
        if not value:
            return None, None
        try:
            parts = value.replace(" ", "").split(",")
            if len(parts) == 2:
                return float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            pass
        return None, None

    @staticmethod
    def _parse_timestamp(value) -> datetime | None:
        """Parse a timestamp (epoch milliseconds or ISO string)."""
        if value is None:
            return None
        try:
            if isinstance(value, (int, float)) and value > 0:
                # ArcGIS typically uses epoch milliseconds
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            if isinstance(value, str) and value:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OSError, OverflowError):
            pass
        return None
