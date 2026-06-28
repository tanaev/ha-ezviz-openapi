"""DataUpdateCoordinator for the EZVIZ Open API integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EzvizApiError, EzvizAuthError, EzvizOpenApi
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class EzvizOpenCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls the camera/device lists; keyed by ``<serial>_<channel>``."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: EzvizOpenApi,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        # Set by __init__ when account credentials are configured (door unlock).
        self.private_api: Any | None = None

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            cameras = await self.api.async_camera_list()
            devices = await self.api.async_device_list()
        except EzvizAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (EzvizApiError, Exception) as err:  # noqa: BLE001 - surface as retryable
            raise UpdateFailed(str(err)) from err

        device_by_serial = {d.get("deviceSerial"): d for d in devices}
        result: dict[str, dict[str, Any]] = {}
        for cam in cameras:
            serial = cam.get("deviceSerial")
            channel = cam.get("channelNo")
            if not serial or channel is None:
                continue
            cam["_device"] = device_by_serial.get(serial, {})
            result[f"{serial}_{channel}"] = cam
        return result
