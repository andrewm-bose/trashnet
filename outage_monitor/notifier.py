"""Send notifications about power outages via email and SMS."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import NotificationConfig
from .kubra_client import Outage

logger = logging.getLogger(__name__)


def format_outage_text(outage: Outage, grocery_info: str = "") -> str:
    """Format a single outage into a readable text block."""
    lines = [
        f"--- Outage {outage.id} ---",
        f"Location: {outage.area_description}",
        f"Customers affected: {outage.customers_affected:,}",
    ]

    if outage.duration_hours is not None:
        hours = outage.duration_hours
        if hours >= 1:
            lines.append(f"Duration: {hours:.1f} hours")
        else:
            lines.append(f"Duration: {int(hours * 60)} minutes")

    if outage.start_time:
        lines.append(f"Started: {outage.start_time.strftime('%Y-%m-%d %H:%M UTC')}")

    if outage.etr:
        lines.append(f"Est. restoration: {outage.etr.strftime('%Y-%m-%d %H:%M UTC')}")

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
    body_lines.append("Data source: Xcel Energy via Kubra StormCenter")

    return subject, "\n".join(body_lines)


def send_notifications(
    outages: list[Outage],
    config: NotificationConfig | None = None,
    grocery_data: dict[str, str] | None = None,
) -> bool:
    """Send notifications about outages via email and SMS.

    Returns True if at least one notification was sent successfully.
    """
    config = config or NotificationConfig()
    subject, body = build_notification(outages, grocery_data)

    if not config.smtp_username or not config.smtp_password:
        logger.error(
            "SMTP credentials not configured. Set SMTP_USERNAME and SMTP_PASSWORD."
        )
        # Print to stdout as fallback so GitHub Actions logs capture it
        print(f"\n{subject}\n{'=' * len(subject)}\n{body}")
        return False

    all_recipients = config.email_recipients + config.sms_recipients
    if not all_recipients:
        logger.error("No recipients configured. Set EMAIL_RECIPIENTS or SMS_RECIPIENTS.")
        print(f"\n{subject}\n{'=' * len(subject)}\n{body}")
        return False

    success = False

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
