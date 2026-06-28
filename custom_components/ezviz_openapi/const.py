"""Constants for the EZVIZ Open API integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ezviz_openapi"
PLATFORMS = [Platform.CAMERA, Platform.LOCK]

# Config / options keys
CONF_APP_KEY = "app_key"
CONF_APP_SECRET = "app_secret"
CONF_REGION = "region"
CONF_VERIFY_SSL = "verify_ssl"
CONF_PROTOCOL = "protocol"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_VERIFY_CODES = "verify_codes"
# Per-entry secret embedded in the stable stream-proxy URL (path-based auth).
CONF_STREAM_TOKEN = "stream_token"
# Optional EZVIZ *account* login (private app API) — only used for door unlock,
# which the Open API (appKey/secret) does not expose.
CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_DOOR_NO = "door_no"


def parse_verify_codes(raw: str) -> dict[str, str]:
    """Parse 'SERIAL=CODE' / 'SERIAL:CODE' pairs (newline or comma separated)."""
    codes: dict[str, str] = {}
    for chunk in (raw or "").replace(",", "\n").splitlines():
        chunk = chunk.strip()
        if not chunk:
            continue
        sep = "=" if "=" in chunk else ":" if ":" in chunk else None
        if not sep:
            continue
        serial, _, code = chunk.partition(sep)
        if serial.strip() and code.strip():
            codes[serial.strip()] = code.strip()
    return codes

# EZVIZ Open Platform regional API gateways (verified live).
# NB: open.ezviz.com is the *website* (404 on API). These hosts serve the API.
REGIONS = {
    "eu": "https://ieuopen.ezvizlife.com",
    "global": "https://isgpopen.ezvizlife.com",
    "us": "https://iusopen.ezvizlife.com",
    "sa": "https://isaopen.ezvizlife.com",
    "china": "https://open.ys7.com",
}

# Private *app* API hosts (pyezvizapi), per region. Login self-corrects to the
# account's real area via the loginArea.apiDomain in the response, so an
# approximate host here is fine.
APP_API_HOSTS = {
    "eu": "apiieu.ezvizlife.com",
    "global": "apiisgp.ezvizlife.com",
    "us": "apius.ezvizlife.com",
    "sa": "apiisa.ezvizlife.com",
    "china": "apiichina.ezvizlife.com",
}

# live/address/get protocol codes
PROTOCOLS = {"hls": 2, "rtmp": 3, "flv": 4}

DEFAULT_REGION = "eu"
# RTMP is a single persistent connection — starts faster and more reliably as a
# Home Assistant stream source than EZVIZ's HLS (slow first segment).
DEFAULT_PROTOCOL = "rtmp"
DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = 120  # seconds; device/channel list refresh cadence
# ISAPI doorNo for RemoteControl (the lock on the door station). 1 is the first
# lock; exposed as an option for stations with multiple locks.
DEFAULT_DOOR_NO = 1
