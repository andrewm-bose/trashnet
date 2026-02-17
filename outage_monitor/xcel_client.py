"""Client for fetching outage data from Xcel Energy via KUBRA StormCenter.

Xcel Energy's outage map is powered by KUBRA's StormCenter platform.
The API flow is:
  1. GET currentState → returns data paths and deployment info
  2. GET configuration → returns layer IDs for cluster data
  3. GET serviceareas.json → returns service area geometry for tiling
  4. GET {quadkey}.json tiles → returns individual outage records

The outage map is at: https://www.outagemap-xcelenergy.com/outagemap/
"""

import logging
import math
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


# ---------------------------------------------------------------------------
# Quadkey / tiling helpers (Web Mercator, same scheme as Bing Maps tiles)
# ---------------------------------------------------------------------------

def _lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Convert lat/lon to tile x, y at a given zoom level."""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _tile_to_quadkey(x: int, y: int, zoom: int) -> str:
    """Convert tile x, y, zoom to a quadkey string."""
    quadkey = []
    for i in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        quadkey.append(str(digit))
    return "".join(quadkey)


def _bounding_box_quadkeys(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float, zoom: int,
) -> list[str]:
    """Return all quadkeys covering a bounding box at the given zoom."""
    x_min, y_max = _lat_lon_to_tile(min_lat, min_lon, zoom)
    x_max, y_min = _lat_lon_to_tile(max_lat, max_lon, zoom)
    keys = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            keys.append(_tile_to_quadkey(x, y, zoom))
    return keys


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decode a Google-encoded polyline string into (lat, lon) pairs.

    This avoids pulling in the ``polyline`` package for a single function.
    Reference: https://developers.google.com/maps/documentation/utilities/polylinealgorithm
    """
    points: list[tuple[float, float]] = []
    idx, lat, lon = 0, 0, 0
    while idx < len(encoded):
        # latitude
        shift, result = 0, 0
        while True:
            b = ord(encoded[idx]) - 63
            idx += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lat += (~(result >> 1) if result & 1 else result >> 1)

        # longitude
        shift, result = 0, 0
        while True:
            b = ord(encoded[idx]) - 63
            idx += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lon += (~(result >> 1) if result & 1 else result >> 1)

        points.append((lat / 1e5, lon / 1e5))
    return points


# ---------------------------------------------------------------------------
# KUBRA StormCenter client
# ---------------------------------------------------------------------------

class XcelOutageClient:
    """Fetches outage data from Xcel Energy via KUBRA StormCenter."""

    # Zoom level for initial tile grid.  7 gives ~1° tiles which is coarse
    # enough to cover the CO service area in a handful of requests.
    MIN_ZOOM = 7
    # Maximum zoom depth when descending into clusters.
    MAX_ZOOM = 14

    def __init__(self, config: KubraConfig | None = None):
        self.config = config or KubraConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "DenverOutageMonitor/1.0",
            "Accept": "application/json",
        })
        # Cached after first call
        self._state: dict | None = None
        self._cluster_url_template: str | None = None

    # -- public API ----------------------------------------------------------

    def fetch_outages(self, state: str = "CO") -> list[Outage]:
        """Fetch all current outages.

        The *state* parameter is kept for API compatibility but is not used by
        the KUBRA backend (outages are scoped by the instance/view IDs and we
        filter geographically later).
        """
        try:
            state_data = self._get_current_state()
            if state_data is None:
                return []

            cluster_template = self._get_cluster_url_template(state_data)
            if cluster_template is None:
                return []

            quadkeys = self._get_initial_quadkeys(state_data)
            logger.info("Starting tile fetch with %d initial quadkeys", len(quadkeys))

            raw_outages: list[dict] = []
            self._descend(quadkeys, cluster_template, raw_outages)
            logger.info("Fetched %d raw outage records", len(raw_outages))

            outages = []
            for raw in raw_outages:
                outage = self._parse_outage(raw)
                if outage:
                    outages.append(outage)

            logger.info("Parsed %d outages with valid geometry", len(outages))
            return outages

        except requests.RequestException as e:
            logger.error("Failed to fetch outage data from KUBRA: %s", e)
            return []

    # -- KUBRA API steps -----------------------------------------------------

    def _get_current_state(self) -> dict | None:
        """Step 1: fetch ``currentState`` for the storm center view."""
        url = (
            f"{self.config.base_url}/stormcenter/api/v1/stormcenters/"
            f"{self.config.instance_id}/views/{self.config.view_id}"
            f"/currentState?preview=false"
        )
        logger.info("Fetching KUBRA currentState")
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self._state = data
        return data

    def _get_cluster_url_template(self, state: dict) -> str | None:
        """Step 2: fetch the configuration to find the cluster layer URL template."""
        deployment_id = state.get("stormcenterDeploymentId")
        if not deployment_id:
            logger.error("No stormcenterDeploymentId in currentState")
            return None

        url = (
            f"{self.config.base_url}/stormcenter/api/v1/stormcenters/"
            f"{self.config.instance_id}/views/{self.config.view_id}"
            f"/configuration/{deployment_id}?preview=false"
        )
        logger.info("Fetching KUBRA configuration (deployment=%s)", deployment_id)
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        config = resp.json()

        # Find the cluster layer in the configuration
        try:
            interval_data = config["config"]["layers"]["data"]["interval_generation_data"]
            cluster_layers = [
                layer for layer in interval_data
                if layer.get("type", "").startswith("CLUSTER_LAYER")
            ]
            if not cluster_layers:
                logger.error("No CLUSTER_LAYER found in configuration")
                return None
            layer_id = cluster_layers[0]["id"]
        except (KeyError, IndexError) as e:
            logger.error("Failed to parse cluster layer from config: %s", e)
            return None

        # Build the URL template using the data path from currentState
        data_path = state.get("data", {}).get("cluster_interval_generation_data", "")
        if not data_path:
            logger.error("No cluster data path in currentState")
            return None

        template = f"{self.config.base_url}/{data_path}/public/{layer_id}/{{quadkey}}.json"
        self._cluster_url_template = template
        logger.info("Cluster URL template: %s", template)
        return template

    def _get_initial_quadkeys(self, state: dict) -> list[str]:
        """Step 3: determine initial quadkeys from service area geometry."""
        try:
            datastatic = state.get("datastatic", {})
            if not datastatic:
                logger.warning("No datastatic in currentState, falling back to CO bounding box")
                return self._colorado_quadkeys()

            regions_key, regions = next(iter(datastatic.items()))
            sa_url = f"{self.config.base_url}/{regions}/{regions_key}/serviceareas.json"
            logger.info("Fetching service areas from %s", sa_url)
            resp = self.session.get(sa_url, timeout=30)
            resp.raise_for_status()
            sa_data = resp.json()

            # Decode all polyline geometries to find the bounding box
            file_data = sa_data.get("file_data", [])
            all_points: list[tuple[float, float]] = []
            for entry in file_data:
                areas = entry.get("geom", {}).get("a", [])
                for encoded in areas:
                    all_points.extend(_decode_polyline(encoded))

            if not all_points:
                logger.warning("No geometry in service areas, falling back to CO bounding box")
                return self._colorado_quadkeys()

            lats = [p[0] for p in all_points]
            lons = [p[1] for p in all_points]
            return _bounding_box_quadkeys(
                min(lats), min(lons), max(lats), max(lons), self.MIN_ZOOM,
            )
        except (requests.RequestException, StopIteration, KeyError) as e:
            logger.warning("Failed to get service areas (%s), falling back to CO bounding box", e)
            return self._colorado_quadkeys()

    def _colorado_quadkeys(self) -> list[str]:
        """Fallback: quadkeys covering Colorado's approximate bounding box."""
        return _bounding_box_quadkeys(36.99, -109.05, 41.0, -102.04, self.MIN_ZOOM)

    def _descend(
        self,
        quadkeys: list[str],
        template: str,
        results: list[dict],
    ) -> None:
        """Recursively fetch tiles, descending into clusters."""
        for qk in quadkeys:
            # KUBRA uses a reversed last-3-chars subdirectory scheme
            qkh = qk[-3:][::-1] if len(qk) >= 3 else qk
            url = template.replace("{quadkey}", f"{qkh}/{qk}")
            try:
                resp = self.session.get(url, timeout=15)
                if not resp.ok:
                    # 404 means no outages in this tile
                    continue
                data = resp.json()
                file_data = data.get("file_data", [])
                if not file_data:
                    continue

                any_clusters = any(
                    entry.get("desc", {}).get("cluster", False)
                    for entry in file_data
                )
                if not any_clusters or len(qk) >= self.MAX_ZOOM:
                    results.extend(file_data)
                else:
                    # Subdivide into 4 child tiles
                    children = [qk + str(i) for i in range(4)]
                    self._descend(children, template, results)

            except (requests.RequestException, ValueError) as e:
                logger.debug("Failed to fetch tile %s: %s", qk, e)

    # -- parsing -------------------------------------------------------------

    def _parse_outage(self, raw: dict) -> Outage | None:
        """Parse a KUBRA outage record into our Outage dataclass."""
        try:
            desc = raw.get("desc", {})
            geom = raw.get("geom", {})

            # Get coordinates
            lat, lon = None, None
            if "p" in geom and geom["p"]:
                # Point geometry — encoded polyline with a single point
                points = _decode_polyline(geom["p"][0])
                if points:
                    lat, lon = points[0]
            elif "a" in geom and geom["a"]:
                # Polygon — compute centroid from first ring
                points = _decode_polyline(geom["a"][0])
                if points:
                    lat = sum(p[0] for p in points) / len(points)
                    lon = sum(p[1] for p in points) / len(points)

            if lat is None or lon is None:
                return None

            customers = int(desc.get("n_out", 0))

            # KUBRA provides limited metadata compared to ArcGIS; extract
            # what's available and default the rest.
            start_time = self._parse_timestamp(desc.get("start_time"))
            etr = self._parse_timestamp(desc.get("etr"))
            cause = str(desc.get("cause", "Unknown"))
            crew_status = str(desc.get("crew_status", desc.get("status", "Unknown")))

            # Location fields may not be present in KUBRA data
            city = str(desc.get("city", ""))
            zip_code = str(desc.get("zip", ""))
            state_val = str(desc.get("state", ""))

            outage_id = str(desc.get("id", desc.get("inc_id", f"{lat}_{lon}")))

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
                source_data=raw,
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug("Failed to parse KUBRA outage: %s - data: %s", e, raw)
            return None

    @staticmethod
    def _parse_timestamp(value) -> datetime | None:
        """Parse a timestamp (epoch milliseconds or ISO string)."""
        if value is None:
            return None
        try:
            if isinstance(value, (int, float)) and value > 0:
                # KUBRA typically uses epoch milliseconds
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            if isinstance(value, str) and value:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OSError, OverflowError):
            pass
        return None
