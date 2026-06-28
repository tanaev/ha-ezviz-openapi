"""Door-station lock entities (remote unlock via the cloud ISAPI passthrough).

One lock per door-station channel. The Open API cannot unlock, so these are only
created when EZVIZ *account* credentials are configured. Door stations have a
momentary relay and report no lock state, so each entity is optimistic: it shows
"unlocked" briefly after a successful command, then returns to "locked".
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DOOR_NO, DEFAULT_DOOR_NO, DOMAIN
from .coordinator import EzvizOpenCoordinator
from .private_api import EzvizPrivateApi, EzvizPrivateError

_LOGGER = logging.getLogger(__name__)

# How long the entity shows "unlocked" after a successful momentary unlock.
_RELOCK_DELAY = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EzvizOpenCoordinator = hass.data[DOMAIN][entry.entry_id]
    private_api: EzvizPrivateApi | None = getattr(coordinator, "private_api", None)
    if private_api is None:
        return  # no account credentials -> no unlock capability

    door_no = int(entry.options.get(CONF_DOOR_NO, DEFAULT_DOOR_NO))
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [
            EzvizDoorLock(coordinator, key, door_no, private_api)
            for key in coordinator.data
            if key not in known
        ]
        for lock in new:
            known.add(lock.lock_key)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class EzvizDoorLock(CoordinatorEntity[EzvizOpenCoordinator], LockEntity):
    """Remote door unlock for one EZVIZ door-station channel."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EzvizOpenCoordinator,
        key: str,
        door_no: int,
        private_api: EzvizPrivateApi,
    ) -> None:
        super().__init__(coordinator)
        self.lock_key = key
        self._door_no = door_no
        self._private_api = private_api
        cam = coordinator.data[key]
        self._serial: str = cam["deviceSerial"]
        self._channel: int = cam["channelNo"]
        self._attr_unique_id = f"{self._serial}_{self._channel}_lock"
        name = cam.get("channelName") or f"Channel {self._channel}"
        self._attr_name = f"{name} lock"
        self._attr_is_locked = True  # optimistic; no real state from the device

    @property
    def _cam(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.lock_key, {})

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._cam.get("_device", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=dev.get("deviceName") or self._serial,
            manufacturer="EZVIZ",
            model=dev.get("model") or dev.get("deviceType"),
            serial_number=self._serial,
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        """Send the remote unlock command (physically opens the door)."""
        try:
            await self.hass.async_add_executor_job(
                self._private_api.unlock, self._serial, self._channel, self._door_no
            )
        except EzvizPrivateError as err:
            raise HomeAssistantError(f"EZVIZ unlock failed: {err}") from err

        # Optimistically reflect the momentary open, then snap back to locked.
        self._attr_is_locked = False
        self.async_write_ha_state()

        @callback
        def _relock(_now: Any) -> None:
            self._attr_is_locked = True
            self.async_write_ha_state()

        async_call_later(self.hass, _RELOCK_DELAY, _relock)

    async def async_lock(self, **kwargs: Any) -> None:
        """No physical lock action — the relay is momentary. Reflect locked."""
        self._attr_is_locked = True
        self.async_write_ha_state()
