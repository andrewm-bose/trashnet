"""Configuration for the Denver Power Outage Monitor."""

import os
from dataclasses import dataclass, field


@dataclass
class OutageCriteria:
    """Criteria for filtering outages worth notifying about."""

    min_customers_affected: int = int(os.getenv("MIN_CUSTOMERS", "100"))
    min_duration_hours: float = float(os.getenv("MIN_DURATION_HOURS", "2.0"))


@dataclass
class KubraConfig:
    """KUBRA StormCenter configuration for Xcel Energy's outage map.

    Xcel's outage map is powered by KUBRA StormCenter.  The instance and
    view IDs are extracted from the live outage map at
    https://www.outagemap-xcelenergy.com/outagemap/
    """

    base_url: str = os.getenv("KUBRA_BASE_URL", "https://kubra.io")
    instance_id: str = os.getenv(
        "KUBRA_INSTANCE_ID", "877fd1e9-4162-473f-b782-d8a53a85326b"
    )
    view_id: str = os.getenv(
        "KUBRA_VIEW_ID", "8fe9d356-96bc-41f1-b353-6720eb408936"
    )


@dataclass
class DenverArea:
    """Bounding box for the Denver metro area (approximate)."""

    min_lat: float = 39.55
    max_lat: float = 39.9
    min_lon: float = -105.2
    max_lon: float = -104.85


@dataclass
class NotificationConfig:
    """Configuration for sending notifications."""

    smtp_server: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    from_email: str = os.getenv("FROM_EMAIL", "")

    # Comma-separated list of email addresses
    email_recipients: list[str] = field(default_factory=lambda: [
        addr.strip()
        for addr in os.getenv("EMAIL_RECIPIENTS", "").split(",")
        if addr.strip()
    ])

    # Comma-separated list of SMS-via-email addresses
    # Format: 5551234567@carrier_gateway
    # Common gateways:
    #   T-Mobile: @tmomail.net
    #   AT&T: @txt.att.net
    #   Verizon: @vtext.com
    #   Sprint: @messaging.sprintpcs.com
    sms_recipients: list[str] = field(default_factory=lambda: [
        addr.strip()
        for addr in os.getenv("SMS_RECIPIENTS", "").split(",")
        if addr.strip()
    ])


@dataclass
class NtfyConfig:
    """Configuration for ntfy.sh push notifications."""

    # ntfy topic URL, e.g. "https://ntfy.sh/your-secret-topic"
    # or a self-hosted instance like "https://ntfy.example.com/topic"
    topic: str = os.getenv("NTFY_TOPIC", "")
    priority: str = os.getenv("NTFY_PRIORITY", "high")


@dataclass
class GroceryConfig:
    """Configuration for finding grocery stores near outages."""

    enabled: bool = os.getenv("GROCERY_SEARCH_ENABLED", "false").lower() == "true"
    google_api_key: str = os.getenv("GOOGLE_PLACES_API_KEY", "")
    search_radius_meters: int = int(os.getenv("GROCERY_SEARCH_RADIUS", "3000"))
    max_results: int = int(os.getenv("GROCERY_MAX_RESULTS", "5"))
