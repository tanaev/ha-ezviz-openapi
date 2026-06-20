"""Stable FLV stream-proxy endpoint.

Gives a fixed URL that never expires; on each connection it fetches a FRESH
EZVIZ live session and pipes the FLV bytes through. The EZVIZ openlive session
is capped at ~60s server-side, so a consumer (Scrypted's rebroadcast, HA's
stream worker, VLC with reconnect) simply reconnects to this same URL and gets
a new session — yielding continuous video without ever handling expiring URLs.

URL: /api/ezviz_openapi/{token}/{serial}/{channel}.flv
The {token} is the per-entry secret (path-based auth, since this view is
unauthenticated so external tools like Scrypted can read it without an HA token).
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EzvizApiError
from .const import (
    CONF_STREAM_TOKEN,
    CONF_VERIFY_CODES,
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PROTOCOLS,
    parse_verify_codes,
)

_LOGGER = logging.getLogger(__name__)

_CHUNK = 64 * 1024


def _entry_for_token(hass: HomeAssistant, token: str) -> ConfigEntry | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_STREAM_TOKEN) == token:
            return entry
    return None


class EzvizStreamView(HomeAssistantView):
    """Proxies a fresh EZVIZ FLV session under a stable, token-protected URL."""

    url = "/api/ezviz_openapi/{token}/{serial}/{channel}.flv"
    name = "api:ezviz_openapi:stream"
    requires_auth = False

    async def get(
        self, request: web.Request, token: str, serial: str, channel: str
    ) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        entry = _entry_for_token(hass, token)
        if entry is None or entry.entry_id not in hass.data.get(DOMAIN, {}):
            return web.Response(status=404, text="unknown stream token")
        try:
            channel_no = int(channel)
        except ValueError:
            return web.Response(status=400, text="bad channel")

        # Send the 200 + headers IMMEDIATELY, before the (slow) EZVIZ fetch and
        # upstream connect. Otherwise a reverse proxy in front of HA times out
        # waiting for the first byte and returns 504. X-Accel-Buffering disables
        # nginx/ingress response buffering so FLV flows through live.
        response = web.StreamResponse(
            headers={
                "Content-Type": "video/x-flv",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        coordinator = hass.data[DOMAIN][entry.entry_id]
        codes = parse_verify_codes(entry.options.get(CONF_VERIFY_CODES, ""))
        verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        session = async_get_clientsession(hass, verify_ssl=verify_ssl)

        upstream = None
        try:
            data = await coordinator.api.async_live_address(
                serial, channel_no, PROTOCOLS["flv"], codes.get(serial)
            )
            url = data.get("url")
            if not url:
                _LOGGER.warning("No EZVIZ live URL for %s/%s", serial, channel)
                return response
            upstream = await session.get(url)
            async for chunk in upstream.content.iter_chunked(_CHUNK):
                await response.write(chunk)
        except (
            EzvizApiError,
            aiohttp.ClientError,
            TimeoutError,
            ConnectionResetError,
            asyncio.CancelledError,
        ) as err:
            _LOGGER.debug("Stream ended for %s/%s: %s", serial, channel, err)
        finally:
            if upstream is not None:
                upstream.close()
        return response
