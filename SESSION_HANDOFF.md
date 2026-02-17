# Denver Power Outage Monitor - Session Handoff

## Project Overview

A Python app that monitors Xcel Energy power outages in the Denver metro area and sends notifications via email (Gmail SMTP) and ntfy push notifications. It optionally lists nearby grocery stores per outage using Google Places API.

**Repos:**
- **Development**: `andrewm-bose/trashnet` (this repo - contains the code alongside a legacy TrashNet ML project)
- **Production**: `andrewm-bose/outages` (separate repo running GitHub Actions on a schedule)

The `outage_monitor/` package in this repo is what runs in production in the `outages` repo.

## Architecture

```
outage_monitor/
  __init__.py
  config.py          - All config via env vars (dataclasses)
  main.py            - Entry point (python -m outage_monitor.main)
  xcel_client.py     - Fetches outages from Xcel's ArcGIS MapServer
  outage_filter.py   - Filters to Denver bounding box + min customers/duration
  grocery_finder.py  - Google Places API nearby search per outage
  notifier.py        - Sends ntfy push + Gmail SMTP emails
```

**Flow:** Fetch outages from ArcGIS -> filter to Denver area -> find nearby grocery stores -> send ntfy + email notifications.

**GitHub Actions:** `.github/workflows/check_outages.yml` runs 4x daily via cron. Secrets/vars are configured in the `outages` repo settings. Entry point: `python -m outage_monitor.main`.

## Current Status

**Working:**
- ArcGIS data fetching (Xcel Energy outages in Colorado)
- Filtering to Denver metro area with configurable thresholds (MIN_CUSTOMERS=20, MIN_DURATION_HOURS=2)
- Email notifications via Gmail SMTP (using App Password)
- Grocery store lookup via Google Places API
- GitHub Actions scheduled workflow
- Test mode (`--test` flag)

## Open Issues

### 1. ntfy notifications not being received

**Symptom:** The log shows `ntfy notification sent to ***` (HTTP 200 success), but no push notification arrives on the user's device.

**How it works:** `notifier.py:108-118` uses `urllib.request.Request` to POST to the URL stored in the `NTFY_TOPIC` secret. The secret must be a **full URL** like `https://ntfy.sh/my-secret-topic` (see `config.py:76-78`).

**Debugging steps to try:**
- Verify `NTFY_TOPIC` secret is a full URL (e.g. `https://ntfy.sh/topic-name`), not just the topic name
- Verify the topic name in the ntfy app subscription matches the secret exactly (case-sensitive)
- Test manually: `curl -d "test" https://ntfy.sh/YOUR_TOPIC` and see if the app receives it
- Check if ntfy app has notifications enabled and isn't being killed by battery optimization
- Could also add logging to print the HTTP response body from ntfy to confirm the server actually accepted it (currently the code only checks for no exception, not the response content)

### 2. Grocery stores are duplicated across all outage locations

**Symptom:** Every outage in the notification shows the same grocery stores, rather than stores specific to each outage's location.

**How it works:** `grocery_finder.py:142-158` iterates through each outage and calls `find_nearby_groceries(outage, config)` which uses `outage.latitude, outage.longitude` at line 98. This *should* produce different results per outage.

**Likely root causes to investigate:**
1. **ArcGIS coordinates may not be granular enough** - The Xcel ArcGIS data might use city-level centroids rather than precise outage locations. Check the actual lat/lng values of filtered outages to see if they're all the same or very close together. Add debug logging like: `logger.info("Searching groceries at %f, %f for outage %s", outage.latitude, outage.longitude, outage.id)`
2. **Denver outages within 3km of each other** - With a 3000m radius and outages in the same part of Denver, the same stores would dominate. Could reduce the radius or deduplicate stores across outages.
3. **Verify with actual data** - Run locally with `LOG_LEVEL=DEBUG` and check the coordinates per outage. If coordinates differ significantly but stores are still identical, it's a Places API issue. If coordinates are nearly identical, it's an ArcGIS data issue.

## Key Config (env vars / GitHub secrets)

| Variable | Type | Notes |
|---|---|---|
| `NTFY_TOPIC` | secret | Full URL: `https://ntfy.sh/topic-name` |
| `NTFY_PRIORITY` | var | Default: `high` |
| `SMTP_USERNAME` | secret | Gmail address |
| `SMTP_PASSWORD` | secret | Gmail **App Password** (not regular password) |
| `FROM_EMAIL` | secret | Sender email |
| `EMAIL_RECIPIENTS` | secret | Comma-separated emails |
| `GOOGLE_PLACES_API_KEY` | secret | Google Cloud API key |
| `GROCERY_SEARCH_ENABLED` | var | `true` to enable |
| `GROCERY_SEARCH_RADIUS` | var | Default: `3000` (meters) |
| `MIN_CUSTOMERS` | var | Default: `100` (currently set to `20` in production) |
| `MIN_DURATION_HOURS` | var | Default: `2.0` |

## Data Source

Xcel Energy ArcGIS MapServer endpoint:
`https://emcs-gis.esriemcs.com/arcgis/rest/services/Xcel/XcelOutage/MapServer/3`

Denver bounding box filter: lat 39.5-39.95, lon -105.2 to -104.6.
