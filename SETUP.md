# Denver Power Outage Monitor - Setup Guide

A free, automated system that monitors Xcel Energy power outages in the Denver
metro area and sends you text/email notifications when significant outages occur.

## How It Works

1. A GitHub Actions workflow runs every 6 hours (free, runs in the cloud)
2. It queries Xcel Energy's outage map via their ArcGIS REST API
3. It filters for outages in the Denver metro area meeting your criteria
4. If matches are found, it sends you an email and/or text message

**Cost: $0/month** - Uses GitHub Actions (free), Gmail SMTP (free), and
carrier SMS gateways (free).

## Setup (One-Time, ~10 minutes)

### Step 1: Set Up Gmail App Password

To send emails/texts, you need a Gmail account with an "App Password":

1. Go to https://myaccount.google.com/security
2. Enable 2-Factor Authentication if you haven't already
3. Go to https://myaccount.google.com/apppasswords
4. Create a new app password (name it "Outage Monitor" or whatever you want)
5. Copy the 16-character password it generates

### Step 2: Configure GitHub Repository Secrets

In your GitHub repository:

1. Go to **Settings** > **Secrets and variables** > **Actions**
2. Add these **Secrets** (click "New repository secret" for each):

| Secret Name | Value |
|---|---|
| `SMTP_USERNAME` | Your Gmail address (e.g., `you@gmail.com`) |
| `SMTP_PASSWORD` | The App Password from Step 1 |
| `FROM_EMAIL` | Same Gmail address |
| `EMAIL_RECIPIENTS` | Comma-separated emails (e.g., `you@gmail.com,friend@gmail.com`) |
| `SMS_RECIPIENTS` | Comma-separated SMS gateways (see below) |

3. Optionally add these **Variables** (under the Variables tab):

| Variable Name | Default | Description |
|---|---|---|
| `MIN_CUSTOMERS` | `100` | Min customers affected to trigger alert |
| `MIN_DURATION_HOURS` | `2.0` | Min outage duration (hours) to trigger alert |

### SMS Gateway Addresses

To send text messages for free, use your phone number + carrier gateway:

| Carrier | Format |
|---|---|
| T-Mobile | `5551234567@tmomail.net` |
| AT&T | `5551234567@txt.att.net` |
| Verizon | `5551234567@vtext.com` |
| Sprint | `5551234567@messaging.sprintpcs.com` |
| US Cellular | `5551234567@email.uscc.net` |
| Metro PCS | `5551234567@mymetropcs.com` |

Multiple recipients: `5551234567@tmomail.net,5559876543@vtext.com`

### Step 3: Test It

1. Go to the **Actions** tab in your GitHub repo
2. Click **Check Denver Power Outages** workflow
3. Click **Run workflow** > **Run workflow**
4. Watch the logs to see if it connects and fetches data

## Optional: Grocery Store Finder

To include nearby grocery stores in outage alerts:

1. Go to https://console.cloud.google.com
2. Create a project (or use existing)
3. Enable the **Places API**
4. Create an API key
5. Add these to your GitHub config:
   - Secret: `GOOGLE_PLACES_API_KEY` = your API key
   - Variable: `GROCERY_SEARCH_ENABLED` = `true`
   - Variable: `GROCERY_SEARCH_RADIUS` = `3000` (meters, optional)

Google gives $200/month free credit, which is far more than this app will use.

## Running Locally

```bash
# Install dependencies
pip install -r requirements-outage.txt

# Copy and edit config
cp .env.example .env
# Edit .env with your values

# Run (with .env loaded)
export $(grep -v '^#' .env | xargs) && python -m outage_monitor.main
```

## Adjusting the Schedule

Edit `.github/workflows/check_outages.yml` and change the cron expression:

```yaml
schedule:
  - cron: '0 0,6,12,18 * * *'  # Every 6 hours
  # - cron: '0 12 * * *'       # Once daily at noon UTC
  # - cron: '0 */4 * * *'      # Every 4 hours
```

## Data Source

This tool queries Xcel Energy's outage map directly via their public ArcGIS
REST API endpoint:

```
https://emcs-gis.esriemcs.com/arcgis/rest/services/Xcel/XcelOutage/MapServer
```

No API key or authentication is required — this is the same endpoint that
powers the public outage map at https://www.outagemap-xcelenergy.com/outagemap/

## Architecture

```
outage_monitor/
├── main.py           # Entry point - orchestrates everything
├── config.py         # All configuration (env vars)
├── xcel_client.py    # Fetches outage data from Xcel's ArcGIS API
├── outage_filter.py  # Filters by location + criteria
├── grocery_finder.py # Optional Google Places integration
└── notifier.py       # Sends email + SMS notifications
```
