"""Gapless stream-proxy endpoint.

The EZVIZ openlive cloud session is capped at ~60s. A naive byte-passthrough
proxy therefore ends every 60s, forcing the consumer (Scrypted) to tear down and
restart with a multi-second gap. Instead, this endpoint produces ONE continuous
MPEG-TS stream that never ends: it chains consecutive EZVIZ sessions, remuxing
each FLV session to MPEG-TS with ffmpeg (-c copy, no transcode) and writing them
all to the same HTTP response. MPEG-TS tolerates the per-session splice, so the
consumer sees an unbroken stream (only a ~1-2s glitch each minute, smoothed by
Scrypted's prebuffer).

URL: /api/ezviz_openapi/{token}/{serial}/{channel}.ts
Token is the per-entry secret (path-based auth; the view is unauthenticated so
external tools like Scrypted can read it without an HA token).
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web

from homeassistant.components.ffmpeg import async_get_ffmpeg_manager
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
_MAX_CONSECUTIVE_FAILURES = 3  # bail if sessions keep failing instantly


def _entry_for_token(hass: HomeAssistant, token: str) -> ConfigEntry | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_STREAM_TOKEN) == token:
            return entry
    return None


class EzvizStreamView(HomeAssistantView):
    """Serves a continuous MPEG-TS stream by chaining EZVIZ sessions."""

    url = "/api/ezviz_openapi/{token}/{serial}/{channel}.ts"
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

        # Send 200 + headers immediately so a reverse proxy in front of HA does
        # not time out (504) while we fetch the first EZVIZ session.
        response = web.StreamResponse(
            headers={
                "Content-Type": "video/mp2t",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        coordinator = hass.data[DOMAIN][entry.entry_id]
        codes = parse_verify_codes(entry.options.get(CONF_VERIFY_CODES, ""))
        verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        session = async_get_clientsession(hass, verify_ssl=verify_ssl)
        ffmpeg_bin = async_get_ffmpeg_manager(hass).binary

        failures = 0
        while failures < _MAX_CONSECUTIVE_FAILURES:
            try:
                produced = await self._pump_one_session(
                    hass, response, coordinator, session, ffmpeg_bin,
                    serial, channel_no, codes.get(serial),
                )
            except (ConnectionResetError, asyncio.CancelledError, aiohttp.ClientError):
                break  # client (Scrypted) disconnected
            failures = 0 if produced else failures + 1
        return response

    async def _pump_one_session(
        self, hass, response, coordinator, session, ffmpeg_bin,
        serial, channel_no, code,
    ) -> bool:
        """Stream one EZVIZ session remuxed to TS. Returns True if data flowed."""
        try:
            data = await coordinator.api.async_live_address(
                serial, channel_no, PROTOCOLS["flv"], code
            )
        except (EzvizApiError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("EZVIZ live URL failed for %s/%s: %s", serial, channel_no, err)
            return False
        url = data.get("url")
        if not url:
            return False

        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts",
            "-i", url,
            "-c", "copy",
            "-f", "mpegts",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        produced = False
        try:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(_CHUNK)
                if not chunk:
                    break  # this session ended -> caller fetches the next one
                produced = True
                await response.write(chunk)
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()
        return produced
