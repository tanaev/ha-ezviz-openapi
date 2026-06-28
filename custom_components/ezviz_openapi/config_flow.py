"""Config and options flow for the EZVIZ Open API integration."""
from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EzvizApiError, EzvizAuthError, EzvizOpenApi
from .const import (
    APP_API_HOSTS,
    CONF_ACCOUNT,
    CONF_APP_KEY,
    CONF_APP_SECRET,
    CONF_LOCK_NO,
    CONF_PASSWORD,
    CONF_PROTOCOL,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_STREAM_TOKEN,
    CONF_VERIFY_CODES,
    CONF_VERIFY_SSL,
    DEFAULT_LOCK_NO,
    DEFAULT_PROTOCOL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PROTOCOLS,
    REGIONS,
)
from .private_api import EzvizPrivateApi, EzvizPrivateError


async def _validate(hass, data: Mapping[str, Any]) -> None:
    """Raise EzvizAuthError / EzvizApiError / ClientError if Open API creds are bad."""
    verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    api = EzvizOpenApi(
        session,
        data[CONF_APP_KEY],
        data[CONF_APP_SECRET],
        REGIONS[data[CONF_REGION]],
        verify_ssl=verify_ssl,
    )
    await api.async_get_token(force=True)
    await api.async_device_list()


async def _validate_account(hass, account: str, password: str, region: str) -> None:
    """Raise EzvizPrivateError if the EZVIZ account login fails."""
    app_host = APP_API_HOSTS.get(region, APP_API_HOSTS[DEFAULT_REGION])
    private = EzvizPrivateApi(account, password, app_host)
    await hass.async_add_executor_job(private.validate)


def _user_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_APP_KEY, default=defaults.get(CONF_APP_KEY, "")): str,
            vol.Required(CONF_APP_SECRET, default=defaults.get(CONF_APP_SECRET, "")): str,
            vol.Required(CONF_REGION, default=defaults.get(CONF_REGION, DEFAULT_REGION)): vol.In(
                list(REGIONS)
            ),
            vol.Required(
                CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            ): bool,
        }
    )


def _app_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(CONF_ACCOUNT, default=defaults.get(CONF_ACCOUNT, "")): str,
            vol.Optional(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
        }
    )


class EzvizOpenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup and reauth."""

    VERSION = 1

    def __init__(self) -> None:
        self._open_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._try(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_APP_KEY])
                self._abort_if_unique_id_configured()
                self._open_data = dict(user_input)
                return await self.async_step_app()
        return self.async_show_form(
            step_id="user", data_schema=_user_schema(user_input), errors=errors
        )

    async def async_step_app(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional EZVIZ account login — enables door unlock. May be skipped."""
        errors: dict[str, str] = {}
        if user_input is not None:
            account = user_input.get(CONF_ACCOUNT, "").strip()
            password = user_input.get(CONF_PASSWORD, "")
            if account and password:
                try:
                    await _validate_account(
                        self.hass, account, password, self._open_data[CONF_REGION]
                    )
                except EzvizPrivateError:
                    errors["base"] = "invalid_account"
                else:
                    self._open_data[CONF_ACCOUNT] = account
                    self._open_data[CONF_PASSWORD] = password
            if not errors:
                self._open_data[CONF_STREAM_TOKEN] = secrets.token_hex(16)
                return self.async_create_entry(
                    title="EZVIZ Open API", data=self._open_data
                )
        return self.async_show_form(
            step_id="app", data_schema=_app_schema(user_input), errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**entry.data, **user_input}
            errors = await self._try(merged)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=merged)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_user_schema(entry.data),
            errors=errors,
        )

    async def _try(self, data: Mapping[str, Any]) -> dict[str, str]:
        try:
            await _validate(self.hass, data)
        except EzvizAuthError:
            return {"base": "invalid_auth"}
        except (EzvizApiError, aiohttp.ClientError, TimeoutError):
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            return {"base": "unknown"}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EzvizOpenOptionsFlow()


class EzvizOpenOptionsFlow(OptionsFlow):
    """Stream protocol, refresh cadence, verify codes, account creds, lock relay."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        opts = self.config_entry.options
        data = self.config_entry.data
        if user_input is not None:
            account = user_input.get(CONF_ACCOUNT, "").strip()
            password = user_input.get(CONF_PASSWORD, "")
            if account and password:
                try:
                    await _validate_account(
                        self.hass, account, password,
                        data.get(CONF_REGION, DEFAULT_REGION),
                    )
                except EzvizPrivateError:
                    errors["base"] = "invalid_account"
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        cur_account = opts.get(CONF_ACCOUNT, data.get(CONF_ACCOUNT, ""))
        cur_password = opts.get(CONF_PASSWORD, data.get(CONF_PASSWORD, ""))
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PROTOCOL,
                    default=opts.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                ): vol.In(list(PROTOCOLS)),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=30, max=3600)),
                vol.Optional(
                    CONF_VERIFY_CODES,
                    default=opts.get(CONF_VERIFY_CODES, ""),
                ): str,
                vol.Optional(CONF_ACCOUNT, default=cur_account): str,
                vol.Optional(CONF_PASSWORD, default=cur_password): str,
                vol.Required(
                    CONF_LOCK_NO,
                    default=opts.get(CONF_LOCK_NO, DEFAULT_LOCK_NO),
                ): vol.All(int, vol.Range(min=0, max=8)),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
