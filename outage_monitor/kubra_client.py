"""Client for fetching outage data from Kubra StormCenter API (powers Xcel Energy's outage map)."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .config import KubraConfig

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
    source_data: dict  # raw data for debugging

    @property
    def duration_hours(self) -> float | None:
        if self.start_time is None:
            return None
        delta = datetime.now(timezone.utc) - self.start_time
        return delta.total_seconds() / 3600

    @property
    def area_description(self) -> str:
        return f"({self.latitude:.4f}, {self.longitude:.4f})"


class KubraClient:
    """Fetches outage data from Kubra's StormCenter API."""

    def __init__(self, config: KubraConfig | None = None):
        self.config = config or KubraConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "DenverOutageMonitor/1.0",
            "Accept": "application/json",
        })

    def fetch_outages(self) -> list[Outage]:
        """Fetch all current outages from Xcel's Kubra-powered outage map."""
        try:
            state = self._get_current_state()
            if not state:
                logger.warning("No current state data available")
                return []

            data_path = self._extract_data_path(state)
            if not data_path:
                logger.warning("Could not extract data path from state")
                return []

            return self._fetch_outage_data(data_path)
        except requests.RequestException as e:
            logger.error("Failed to fetch outage data: %s", e)
            return []

    def _get_current_state(self) -> dict | None:
        """Get the current deployment state from Kubra."""
        url = (
            f"{self.config.base_url}/stormcenter/api/v1/stormcenters"
            f"/{self.config.instance_id}/views/{self.config.view_id}"
            f"/currentState?preview=false"
        )
        logger.info("Fetching current state from: %s", url)
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _extract_data_path(self, state: dict) -> str | None:
        """Extract the data path from the current state response."""
        # The state response contains a reference to where the actual
        # outage data files are stored
        data = state.get("data", {})
        if not data:
            # Try alternate structure
            for key in state:
                if isinstance(state[key], dict) and "datastatic" in str(state[key]):
                    data = state[key]
                    break

        # Look for the data path in various possible locations
        for key in ["datastatic", "directory", "config"]:
            if key in data:
                return data[key]

        # Try to find it by looking for a URL pattern
        state_str = json.dumps(state)
        match = re.search(r'(data/[a-f0-9-]+/public)', state_str)
        if match:
            return match.group(1)

        # Return the full state for the caller to inspect
        logger.debug("Full state response: %s", json.dumps(state, indent=2))
        return None

    def _fetch_outage_data(self, data_path: str) -> list[Outage]:
        """Fetch actual outage data from the data path."""
        # Try fetching the summary first
        summary_url = f"{self.config.base_url}/{data_path}/public/summary-1/data.json"
        logger.info("Fetching outage summary from: %s", summary_url)

        try:
            resp = self.session.get(summary_url, timeout=30)
            resp.raise_for_status()
            summary = resp.json()
        except requests.RequestException:
            logger.warning("Summary endpoint failed, trying alternate paths")
            summary = None

        outages = []

        if summary and "file_data" in summary:
            for item in summary.get("file_data", []):
                outage = self._parse_outage(item)
                if outage:
                    outages.append(outage)
        elif summary and "summaryFileData" in summary:
            file_data = summary["summaryFileData"]
            if isinstance(file_data, dict) and "areas" in file_data:
                for area in file_data["areas"]:
                    for outage_data in area.get("outages", []):
                        outage = self._parse_outage(outage_data)
                        if outage:
                            outages.append(outage)

        # Also try fetching individual quadkey tiles for more detail
        if not outages:
            outages = self._fetch_quadkey_data(data_path)

        logger.info("Found %d outages", len(outages))
        return outages

    def _fetch_quadkey_data(self, data_path: str) -> list[Outage]:
        """Fetch outage data from quadkey tiles covering the Denver area."""
        import mercantile

        # Denver metro area bounds
        outages = []
        zoom = 8  # Start at a reasonable zoom level

        # Get tiles that cover the Denver metro area
        tiles = list(mercantile.tiles(-105.2, 39.5, -104.6, 39.95, zooms=zoom))

        for tile in tiles:
            quadkey = mercantile.quadkey(tile)
            tile_url = (
                f"{self.config.base_url}/{data_path}"
                f"/public/cluster-1/{quadkey}.json"
            )
            try:
                resp = self.session.get(tile_url, timeout=15)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                tile_data = resp.json()

                for item in tile_data.get("file_data", tile_data.get("data", [])):
                    outage = self._parse_outage(item)
                    if outage:
                        outages.append(outage)
            except requests.RequestException:
                continue

        return outages

    def _parse_outage(self, data: dict) -> Outage | None:
        """Parse a single outage record from various possible formats."""
        try:
            # Extract location - try multiple field names
            lat = data.get("lat") or data.get("latitude") or data.get("y")
            lon = data.get("lng") or data.get("longitude") or data.get("x")

            # Try nested geometry
            if lat is None and "geom" in data:
                geom = data["geom"]
                if isinstance(geom, dict):
                    lat = geom.get("lat") or geom.get("y")
                    lon = geom.get("lng") or geom.get("x")

            # Try point coordinates
            if lat is None and "point" in data:
                point = data["point"]
                if isinstance(point, list) and len(point) >= 2:
                    lon, lat = point[0], point[1]

            if lat is None or lon is None:
                logger.debug("No coordinates found in outage data: %s", data)
                return None

            lat = float(lat)
            lon = float(lon)

            # Extract customer count
            customers = (
                data.get("numPeople")
                or data.get("cust_a", {}).get("val", 0)
                or data.get("customersAffected")
                or data.get("customers_affected")
                or data.get("n_out")
                or 0
            )
            customers = int(customers)

            # Extract start time
            start_time = None
            for time_field in ["startTime", "start_time", "outageStartTime", "crewDispatchTime"]:
                if time_field in data and data[time_field]:
                    try:
                        ts = data[time_field]
                        if isinstance(ts, (int, float)):
                            start_time = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                        else:
                            start_time = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    except (ValueError, OSError):
                        pass
                    break

            # Extract ETR
            etr = None
            for etr_field in ["etr", "estRestTime", "estimatedRestorationTime", "etrTime"]:
                if etr_field in data and data[etr_field]:
                    try:
                        ts = data[etr_field]
                        if isinstance(ts, (int, float)):
                            etr = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                        else:
                            etr = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    except (ValueError, OSError):
                        pass
                    break

            # Extract cause and crew status
            cause = (
                data.get("cause", {}).get("val", "")
                if isinstance(data.get("cause"), dict)
                else str(data.get("cause", "Unknown"))
            )
            crew_status = (
                data.get("crewStatus", {}).get("val", "")
                if isinstance(data.get("crewStatus"), dict)
                else str(data.get("crew_status", data.get("crewStatus", "Unknown")))
            )

            return Outage(
                id=str(data.get("id", data.get("outageId", f"{lat}_{lon}"))),
                latitude=lat,
                longitude=lon,
                customers_affected=customers,
                start_time=start_time,
                etr=etr,
                cause=cause,
                crew_status=crew_status,
                source_data=data,
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug("Failed to parse outage: %s - data: %s", e, data)
            return None
