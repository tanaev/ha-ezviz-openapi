"""Camera entities backed by the EZVIZ Open API."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EzvizApiError
from .const import (
    CONF_PROTOCOL,
    CONF_STREAM_TOKEN,
    CONF_VERIFY_CODES,
    DEFAULT_PROTOCOL,
    DOMAIN,
    PROTOCOLS,
    parse_verify_codes,
)
from .coordinator import EzvizOpenCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EzvizOpenCoordinator = hass.data[DOMAIN][entry.entry_id]
    protocol = PROTOCOLS[entry.options.get(CONF_PROTOCOL, DEFAULT_PROTOCOL)]
    codes = parse_verify_codes(entry.options.get(CONF_VERIFY_CODES, ""))

    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [
            EzvizOpenCamera(coordinator, key, protocol, codes)
            for key in coordinator.data
            if key not in known
        ]
        for cam in new:
            known.add(cam.camera_key)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class EzvizOpenCamera(CoordinatorEntity[EzvizOpenCoordinator], Camera):
    """A single EZVIZ channel exposed as a HA camera."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM
    # Each still opens a short-lived cloud session, so don't refresh too often.
    _attr_frame_interval = 30.0

    def __init__(
        self,
        coordinator: EzvizOpenCoordinator,
        key: str,
        protocol: int,
        codes: dict[str, str],
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self.camera_key = key
        self._protocol = protocol
        self._codes = codes
        cam = coordinator.data[key]
        self._serial: str = cam["deviceSerial"]
        self._channel: int = cam["channelNo"]
        self._attr_unique_id = f"{self._serial}_{self._channel}"
        self._attr_name = cam.get("channelName") or f"Channel {self._channel}"

    @property
    def _cam(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.camera_key, {})

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.camera_key in self.coordinator.data
            and self._cam.get("status") == 1
        )

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._cam.get("_device", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=dev.get("deviceName") or self._serial,
            manufacturer="EZVIZ",
            model=dev.get("model") or dev.get("deviceType"),
            serial_number=self._serial,
            sw_version=dev.get("deviceVersion"),
        )

    def _proxy_url(self) -> str | None:
        """Stable, non-expiring FLV URL served by this integration.

        It transparently fetches a fresh EZVIZ session on every connect, so any
        consumer that reconnects (HA's stream worker, Scrypted's rebroadcast,
        VLC) gets continuous video despite the ~60s server-side session cap.
        """
        token = self.coordinator.config_entry.data.get(CONF_STREAM_TOKEN)
        if not token:
            return None
        try:
            base = get_url(self.hass, prefer_external=False, allow_internal=True)
        except NoURLAvailableError:
            base = "http://127.0.0.1:8123"
        return f"{base}/api/ezviz_openapi/{token}/{self._serial}/{self._channel}.ts"

    async def stream_source(self) -> str | None:
        """Return the stable proxy URL (it refreshes the EZVIZ session itself)."""
        return self._proxy_url()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the stable stream URL so it can be pasted into Scrypted/VLC."""
        url = self._proxy_url()
        return {"stream_url": url} if url else {}

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Still image: grab one frame from the live stream via ffmpeg.

        The EZVIZ ``device/capture`` snapshot API needs a paid cloud package
        (error 10026), so instead we open a fresh FLV live URL and let HA's
        bundled ffmpeg pull a single keyframe — no subscription required.
        """
        try:
            data = await self.coordinator.api.async_live_address(
                self._serial,
                self._channel,
                PROTOCOLS["flv"],
                self._codes.get(self._serial),
            )
        except (EzvizApiError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Snapshot URL failed for %s: %s", self.unique_id, err)
            return None
        url = data.get("url")
        if not url:
            return None
        try:
            return await async_get_image(self.hass, url, width=width, height=height)
        except HomeAssistantError as err:
            _LOGGER.debug("Snapshot frame grab failed for %s: %s", self.unique_id, err)
            return None
