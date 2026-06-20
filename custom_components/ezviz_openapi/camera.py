"""Camera entities backed by the EZVIZ Open API."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EzvizApiError
from .const import (
    CONF_PROTOCOL,
    CONF_VERIFY_CODES,
    DEFAULT_PROTOCOL,
    DOMAIN,
    PROTOCOLS,
)
from .coordinator import EzvizOpenCoordinator

_LOGGER = logging.getLogger(__name__)


def _parse_codes(raw: str) -> dict[str, str]:
    """Parse 'SERIAL=CODE' / 'SERIAL:CODE' pairs (newline or comma separated)."""
    codes: dict[str, str] = {}
    for chunk in raw.replace(",", "\n").splitlines():
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EzvizOpenCoordinator = hass.data[DOMAIN][entry.entry_id]
    protocol = PROTOCOLS[entry.options.get(CONF_PROTOCOL, DEFAULT_PROTOCOL)]
    codes = _parse_codes(entry.options.get(CONF_VERIFY_CODES, ""))

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

    async def stream_source(self) -> str | None:
        """Fetch a *fresh* live URL on each (re)start — this is the auto-refresh."""
        try:
            data = await self.coordinator.api.async_live_address(
                self._serial,
                self._channel,
                self._protocol,
                self._codes.get(self._serial),
            )
        except (EzvizApiError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Failed to get live URL for %s: %s", self.unique_id, err)
            return None
        return data.get("url")

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Still image via the EZVIZ capture endpoint (best-effort)."""
        try:
            url = await self.coordinator.api.async_capture(self._serial, self._channel)
            if url:
                return await self.coordinator.api.async_fetch_image(url)
        except (EzvizApiError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Snapshot failed for %s: %s", self.unique_id, err)
        return None
