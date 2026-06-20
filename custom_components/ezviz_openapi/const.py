"""Constants for the EZVIZ Open API integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ezviz_openapi"
PLATFORMS = [Platform.CAMERA]

# Config / options keys
CONF_APP_KEY = "app_key"
CONF_APP_SECRET = "app_secret"
CONF_REGION = "region"
CONF_VERIFY_SSL = "verify_ssl"
CONF_PROTOCOL = "protocol"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_VERIFY_CODES = "verify_codes"

# EZVIZ Open Platform regional API gateways (verified live).
# NB: open.ezviz.com is the *website* (404 on API). These hosts serve the API.
REGIONS = {
    "eu": "https://ieuopen.ezvizlife.com",
    "global": "https://isgpopen.ezvizlife.com",
    "us": "https://iusopen.ezvizlife.com",
    "sa": "https://isaopen.ezvizlife.com",
    "china": "https://open.ys7.com",
}

# live/address/get protocol codes
PROTOCOLS = {"hls": 2, "rtmp": 3, "flv": 4}

DEFAULT_REGION = "eu"
DEFAULT_PROTOCOL = "hls"
DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = 120  # seconds; device/channel list refresh cadence
