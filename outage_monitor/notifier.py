"""Send notifications about power outages via email, SMS, and ntfy."""

import logging
import smtplib
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from .config import NotificationConfig, NtfyConfig
from .xcel_client import Outage

logger = logging.getLogger(__name__)

_DENVER_TZ = ZoneInfo("America/Denver")


def _fmt_time(dt) -> str:
    """Format a datetime in Mountain time (MST/MDT)."""
    local = dt.astimezone(_DENVER_TZ)
    # e.g. "2026-02-17 03:45 PM MST"
    tz_abbr = local.strftime("%Z")  # MST or MDT depending on DST
    return local.strftime(f"%Y-%m-%d %I:%M %p {tz_abbr}")


def format_outage_text(outage: Outage, grocery_info: str = "") -> str:
    """Format a single outage into a readable text block."""
    lines = [
        f"--- Outage {outage.id} ---",
        f"Location: {outage.area_description}",
        f"Coordinates: {outage.latitude:.5f}, {outage.longitude:.5f}",
        f"Customers affected: {outage.customers_affected:,}",
    ]

    if outage.duration_hours is not None:
        hours = outage.duration_hours
        if hours >= 1:
            lines.append(f"Duration: {hours:.1f} hours")
        else:
            lines.append(f"Duration: {int(hours * 60)} minutes")

    if outage.start_time:
        lines.append(f"Started: {_fmt_time(outage.start_time)}")

    if outage.etr:
        lines.append(f"Est. restoration: {_fmt_time(outage.etr)}")

    lines.append(f"Cause: {outage.cause}")
    lines.append(f"Crew status: {outage.crew_status}")

    if grocery_info:
        lines.append(f"\nNearby grocery stores:\n{grocery_info}")

    return "\n".join(lines)


def build_notification(
    outages: list[Outage],
    grocery_data: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Build the notification subject and body.

    Returns (subject, body) tuple.
    """
    grocery_data = grocery_data or {}
    count = len(outages)
    total_customers = sum(o.customers_affected for o in outages)

    subject = (
        f"Denver Power Outage Alert: {count} outage{'s' if count != 1 else ''}"
        f" ({total_customers:,} customers)"
    )

    body_lines = [
        f"Denver Power Outage Alert",
        f"========================",
        f"",
        f"{count} outage{'s' if count != 1 else ''} detected meeting your criteria.",
        f"Total customers affected: {total_customers:,}",
        f"",
    ]

    for outage in sorted(outages, key=lambda o: o.customers_affected, reverse=True):
        grocery_info = grocery_data.get(outage.id, "")
        body_lines.append(format_outage_text(outage, grocery_info))
        body_lines.append("")

    body_lines.append("---")
    body_lines.append("Denver Power Outage Monitor")
    body_lines.append("Data source: Xcel Energy via ArcGIS MapServer")

    return subject, "\n".join(body_lines)


def send_ntfy(
    subject: str,
    body: str,
    config: NtfyConfig | None = None,
) -> bool:
    """Send a push notification via ntfy.sh.

    Returns True if the notification was sent successfully.
    """
    config = config or NtfyConfig()
    if not config.topic:
        return False

    try:
        req = urllib.request.Request(
            config.topic,
            data=body.encode("utf-8"),
            headers={
                "Title": subject,
                "Priority": config.priority,
                "Tags": "zap",
            },
        )
        urllib.request.urlopen(req, timeout=15)
        logger.info("ntfy notification sent to %s", config.topic)
        return True
    except (urllib.error.URLError, OSError) as e:
        logger.error("Failed to send ntfy notification: %s", e)
        return False


def send_notifications(
    outages: list[Outage],
    config: NotificationConfig | None = None,
    ntfy_config: NtfyConfig | None = None,
    grocery_data: dict[str, str] | None = None,
) -> bool:
    """Send notifications about outages via email, SMS, and ntfy.

    Returns True if at least one notification was sent successfully.
    """
    config = config or NotificationConfig()
    ntfy_config = ntfy_config or NtfyConfig()
    subject, body = build_notification(outages, grocery_data)

    success = False

    # Send ntfy push notification (independent of SMTP)
    if ntfy_config.topic:
        if send_ntfy(subject, body, ntfy_config):
            success = True

    # Check SMTP config for email/SMS
    has_smtp = config.smtp_username and config.smtp_password
    all_recipients = config.email_recipients + config.sms_recipients

    if all_recipients and not has_smtp:
        logger.error(
            "SMTP credentials not configured. Set SMTP_USERNAME and SMTP_PASSWORD."
        )

    if not all_recipients and not ntfy_config.topic:
        logger.error(
            "No recipients configured. Set EMAIL_RECIPIENTS, SMS_RECIPIENTS, or NTFY_TOPIC."
        )

    if has_smtp and all_recipients:
        try:
            with smtplib.SMTP(config.smtp_server, config.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(config.smtp_username, config.smtp_password)

                # Send full email to email recipients
                for recipient in config.email_recipients:
                    try:
                        msg = MIMEMultipart()
                        msg["From"] = config.from_email or config.smtp_username
                        msg["To"] = recipient
                        msg["Subject"] = subject
                        msg.attach(MIMEText(body, "plain"))
                        server.send_message(msg)
                        logger.info("Email sent to %s", recipient)
                        success = True
                    except smtplib.SMTPException as e:
                        logger.error("Failed to send email to %s: %s", recipient, e)

                # Send abbreviated SMS to SMS recipients (carrier gateways have
                # character limits, typically 160 chars per segment)
                sms_body = _build_sms_body(outages)
                for recipient in config.sms_recipients:
                    try:
                        msg = MIMEMultipart()
                        msg["From"] = config.from_email or config.smtp_username
                        msg["To"] = recipient
                        msg["Subject"] = ""  # SMS doesn't use subject
                        msg.attach(MIMEText(sms_body, "plain"))
                        server.send_message(msg)
                        logger.info("SMS sent to %s", recipient)
                        success = True
                    except smtplib.SMTPException as e:
                        logger.error("Failed to send SMS to %s: %s", recipient, e)

        except (smtplib.SMTPException, OSError) as e:
            logger.error("SMTP connection failed: %s", e)

    if not success:
        # Print to stdout as fallback so GitHub Actions logs capture it
        print(f"\n{subject}\n{'=' * len(subject)}\n{body}")

    return success


def _build_sms_body(outages: list[Outage]) -> str:
    """Build a short SMS-friendly message."""
    count = len(outages)
    total = sum(o.customers_affected for o in outages)
    lines = [f"Denver Power Alert: {count} outage(s), {total:,} customers"]

    for outage in sorted(outages, key=lambda o: o.customers_affected, reverse=True)[:3]:
        duration = ""
        if outage.duration_hours is not None:
            duration = f", {outage.duration_hours:.0f}h"
        lines.append(f"- {outage.customers_affected:,} cust{duration} @ {outage.area_description}")

    if count > 3:
        lines.append(f"+ {count - 3} more")

    return "\n".join(lines)
