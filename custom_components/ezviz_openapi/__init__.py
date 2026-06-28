"""The EZVIZ Open API integration."""
from __future__ import annotations

import secrets

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EzvizOpenApi
from .const import (
    APP_API_HOSTS,
    CONF_ACCOUNT,
    CONF_APP_KEY,
    CONF_APP_SECRET,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_STREAM_TOKEN,
    CONF_VERIFY_SSL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    REGIONS,
)
from .coordinator import EzvizOpenCoordinator
from .private_api import EzvizPrivateApi
from .view import EzvizStreamView

type EzvizConfigEntry = ConfigEntry

_VIEW_REGISTERED = "stream_view_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Backfill a stable stream-proxy token for entries created before v0.2.
    if not entry.data.get(CONF_STREAM_TOKEN):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_STREAM_TOKEN: secrets.token_hex(16)}
        )

    # Register the stream-proxy HTTP view once for the whole integration.
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get(_VIEW_REGISTERED):
        hass.http.register_view(EzvizStreamView())
        domain_data[_VIEW_REGISTERED] = True

    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    api = EzvizOpenApi(
        session,
        entry.data[CONF_APP_KEY],
        entry.data[CONF_APP_SECRET],
        REGIONS[entry.data[CONF_REGION]],
        verify_ssl=verify_ssl,
    )
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = EzvizOpenCoordinator(hass, entry, api, scan_interval)

    # Optional private-account API for door unlock (Open API can't unlock).
    # Options take precedence so creds can be added/changed after setup.
    account = entry.options.get(CONF_ACCOUNT) or entry.data.get(CONF_ACCOUNT)
    password = entry.options.get(CONF_PASSWORD) or entry.data.get(CONF_PASSWORD)
    if account and password:
        region = entry.data.get(CONF_REGION, DEFAULT_REGION)
        app_host = APP_API_HOSTS.get(region, APP_API_HOSTS[DEFAULT_REGION])
        coordinator.private_api = EzvizPrivateApi(account, password, app_host)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
