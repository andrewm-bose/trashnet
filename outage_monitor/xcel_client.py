"""Client for fetching outage data from Xcel Energy's ArcGIS REST API.

Xcel's outage map is served by an Esri ArcGIS MapServer at:
  https://emcs-gis.esriemcs.com/arcgis/rest/services/Xcel/XcelOutage/MapServer

Layer 2 contains individual outage records for Colorado (and other states).
The ArcGIS REST API is well-documented: https://developers.arcgis.com/rest/
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


class XcelOutageClient:
    """Fetches outage data from Xcel Energy's ArcGIS MapServer."""

    def __init__(self, config: ArcGISConfig | None = None):
        self.config = config or ArcGISConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "DenverOutageMonitor/1.0",
            "Accept": "application/json",
        })

    def fetch_outages(self, state: str = "CO") -> list[Outage]:
        """Fetch all current outages for a given state.

        Uses the ArcGIS REST query endpoint with pagination to retrieve
        all outage features.
        """
        try:
            # First, get total count
            count = self._get_outage_count(state)
            logger.info("Total outages for state=%s: %d", state, count)

            if count == 0:
                return []

            # Fetch all outage features (paginated if needed)
            return self._fetch_all_features(state)

        except requests.RequestException as e:
            logger.error("Failed to fetch outage data: %s", e)
            return []

    def _get_outage_count(self, state: str) -> int:
        """Get the total count of outages for a state."""
        params = {
            "f": "json",
            "returnCountOnly": "true",
            "returnIdsOnly": "false",
            "returnGeometry": "false",
            "spatialRel": "esriSpatialRelIntersects",
            "where": f"states like '%{state}%'",
        }
        resp = self.session.get(self.config.query_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("count", 0)

    def _fetch_all_features(self, state: str) -> list[Outage]:
        """Fetch all outage features, handling ArcGIS pagination."""
        all_outages = []
        offset = 0
        batch_size = 1000  # ArcGIS default max record count

        while True:
            params = {
                "f": "json",
                "where": f"states like '%{state}%'",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",  # WGS84 lat/lon coordinates
                "spatialRel": "esriSpatialRelIntersects",
                "resultOffset": str(offset),
                "resultRecordCount": str(batch_size),
            }

            logger.info("Fetching outages offset=%d", offset)
            resp = self.session.get(self.config.query_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.error("ArcGIS API error: %s", data["error"])
                break

            features = data.get("features", [])
            if not features:
                break

            for feature in features:
                outage = self._parse_feature(feature)
                if outage:
                    all_outages.append(outage)

            # Check if there are more results
            exceeded = data.get("exceededTransferLimit", False)
            if not exceeded or len(features) < batch_size:
                break

            offset += len(features)

        logger.info("Fetched %d outages total", len(all_outages))
        return all_outages

    def _parse_feature(self, feature: dict) -> Outage | None:
        """Parse an ArcGIS feature into an Outage object."""
        try:
            attrs = feature.get("attributes", {})
            geom = feature.get("geometry", {})

            # Get coordinates - ArcGIS returns centroid or point geometry
            # With outSR=4326, coordinates are in lat/lon
            lat = None
            lon = None

            if "y" in geom and "x" in geom:
                lat = float(geom["y"])
                lon = float(geom["x"])
            elif "rings" in geom and geom["rings"]:
                # Polygon — calculate centroid from first ring
                ring = geom["rings"][0]
                if ring:
                    lon = sum(p[0] for p in ring) / len(ring)
                    lat = sum(p[1] for p in ring) / len(ring)

            if lat is None or lon is None:
                logger.debug("No coordinates in feature: %s", feature)
                return None

            # Parse timestamps (ArcGIS typically uses epoch milliseconds)
            start_time = self._parse_timestamp(
                attrs.get("outageStartTime")
                or attrs.get("start_time")
                or attrs.get("crewDispatchTime")
            )
            etr = self._parse_timestamp(
                attrs.get("etr")
                or attrs.get("estimatedRestorationTime")
                or attrs.get("estRestTime")
            )

            # Extract fields — field names may vary; try common patterns
            customers = int(
                attrs.get("custAffected", 0)
                or attrs.get("customersAffected", 0)
                or attrs.get("numCustAffected", 0)
                or attrs.get("cust_a", 0)
                or attrs.get("numPeople", 0)
                or 0
            )

            cause = str(attrs.get("cause", attrs.get("outCause", "Unknown")))
            crew_status = str(attrs.get("crewStatus", attrs.get("crewStat", "Unknown")))
            city = str(attrs.get("city", attrs.get("CITY", "")))
            zip_code = str(attrs.get("zip", attrs.get("ZIP", attrs.get("zipCode", ""))))
            state_val = str(attrs.get("states", attrs.get("state", "")))
            outage_id = str(
                attrs.get("OBJECTID", attrs.get("outageId", attrs.get("id", f"{lat}_{lon}")))
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
                zip_code=zip_code,
                state=state_val,
                source_data=attrs,
            )

        except (KeyError, TypeError, ValueError) as e:
            logger.debug("Failed to parse feature: %s - data: %s", e, feature)
            return None

    @staticmethod
    def _parse_timestamp(value) -> datetime | None:
        """Parse a timestamp from ArcGIS (typically epoch milliseconds)."""
        if value is None:
            return None
        try:
            if isinstance(value, (int, float)) and value > 0:
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            if isinstance(value, str) and value:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OSError, OverflowError):
            pass
        return None
