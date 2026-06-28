"""Private EZVIZ *app* API wrapper — door unlock only.

The Open Platform API (appKey/appSecret) cannot unlock a door; that command only
exists in the private account API. This is a thin, synchronous wrapper over
``pyezvizapi`` (account login -> sessionId -> PUT IoT unlock). It is blocking
(``requests``-based), so Home Assistant must call it via the executor.
"""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class EzvizPrivateError(Exception):
    """Login or unlock failed on the private app API."""


class EzvizPrivateApi:
    """Login + remote unlock via pyezvizapi (lazy login, auto re-login once)."""

    def __init__(self, account: str, password: str, app_host: str) -> None:
        self._account = account
        self._password = password
        self._app_host = app_host
        self._client: Any | None = None
        self._user_id: str | None = None

    # All methods below are SYNC — run them via hass.async_add_executor_job.
    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from pyezvizapi.client import EzvizClient  # noqa: PLC0415

            client = EzvizClient(self._account, self._password, self._app_host)
            client.login()
            try:
                self._user_id = (client.get_user_id() or {}).get("userId")
            except Exception:  # noqa: BLE001 - user id is best-effort
                self._user_id = None
            self._client = client
            return client
        except Exception as err:  # noqa: BLE001 - normalize to our error type
            raise EzvizPrivateError(f"EZVIZ account login failed: {err}") from err

    def validate(self) -> None:
        """Log in once to confirm the account credentials work."""
        self._ensure_client()

    def _do_unlock(self, serial: str, lock_no: int) -> None:
        client = self._ensure_client()
        user_id = self._user_id or self._account
        # bindCode / userName ('Hassio') are resolved from the terminal bind by
        # the library (use_terminal_bind=True default).
        client.remote_unlock(serial, user_id, lock_no)

    def unlock(self, serial: str, lock_no: int) -> None:
        """Unlock the given door-station relay; re-login once on failure."""
        try:
            self._do_unlock(serial, lock_no)
        except Exception as err:  # noqa: BLE001 - retry with a fresh session
            _LOGGER.debug("Unlock failed (%s), re-logging in: %s", serial, err)
            self._client = None
            try:
                self._do_unlock(serial, lock_no)
            except Exception as err2:  # noqa: BLE001
                raise EzvizPrivateError(f"Unlock failed for {serial}: {err2}") from err2
