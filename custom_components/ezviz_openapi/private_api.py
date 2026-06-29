"""Private EZVIZ/Hik-Connect *app* API wrapper — door unlock only.

The Open Platform API (appKey/appSecret) cannot unlock a door. Neither does the
EZVIZ cloud "DoorLockMgr" route work for Hik video door stations (the device
reports the feature as not registered). What the official Hik-Connect app uses,
and what actually works here, is the cloud **ISAPI passthrough**:

    POST https://{api}/v3/userdevices/v1/isapi
    form: deviceSerial, channelNo, apiKey, apiData="<METHOD> <ISAPI-URL>\\r\\n<body>"

The unlock command is an ISAPI RemoteControl door open, addressed to the door
station added to the indoor unit (selected by ``channelNo`` *inside* the body)::

    PUT /ISAPI/AccessControl/RemoteControl/door/<doorNo>
    <RemoteControlDoor><cmd>open</cmd>
        <channelNo>N</channelNo><controlType>monitor</controlType></RemoteControlDoor>

``channelNo`` + ``controlType`` are mandatory for the indoor station — without
them the device replies ``methodNotAllowed``.

This wrapper uses ``pyezvizapi`` only for the account login (MD5 password, area
redirect, sessionId, re-login); the passthrough POST is sent over that
authenticated session. It is blocking (``requests``), so Home Assistant must run
it via the executor.
"""
from __future__ import annotations

import logging
import time

_LOGGER = logging.getLogger(__name__)

# Generic non-empty routing key accepted by the proxy (it routes on the ISAPI
# path in apiData, not on this value; an empty key is rejected with 400).
_API_KEY = "100163"
# Transient "device network abnormal" — the cloud tunnel to the device is waking
# up. Poll quickly so we catch it the moment it responds (~0.7s warm).
_DEVICE_NET_ERR = "2009"
_MAX_TRIES = 6
_RETRY_DELAY = 0.5


class EzvizPrivateError(Exception):
    """Login or unlock failed on the private app API."""

    def __init__(self, message: str, *, tunnel: bool = False) -> None:
        super().__init__(message)
        # tunnel=True -> the device cloud link was unreachable (2009); a
        # re-login would not help, so callers should not retry it as auth.
        self.tunnel = tunnel


class EzvizPrivateApi:
    """Account login + cloud ISAPI door unlock (lazy login, auto re-login once)."""

    def __init__(self, account: str, password: str, app_host: str) -> None:
        self._account = account
        self._password = password
        self._app_host = app_host
        self._client = None

    # All methods below are SYNC — run them via hass.async_add_executor_job.
    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from pyezvizapi.client import EzvizClient  # noqa: PLC0415

            client = EzvizClient(self._account, self._password, self._app_host)
            client.login()
            self._client = client
            return client
        except Exception as err:  # noqa: BLE001 - normalize to our error type
            raise EzvizPrivateError(f"EZVIZ account login failed: {err}") from err

    def validate(self) -> None:
        """Log in once to confirm the account credentials work."""
        self._ensure_client()

    def _passthrough(self, client, serial: str, channel_no: int, api_data: str) -> str:
        host = client._token["api_url"]  # noqa: SLF001 - set by pyezvizapi login
        url = f"https://{host}/v3/userdevices/v1/isapi"
        payload = {
            "deviceSerial": serial,
            "channelNo": str(channel_no),
            "apiKey": _API_KEY,
            "apiData": api_data,
        }
        started = time.monotonic()
        for attempt in range(_MAX_TRIES):
            resp = client._session.post(url, data=payload, timeout=20)  # noqa: SLF001
            text = resp.text
            if _DEVICE_NET_ERR not in text:
                if attempt:
                    _LOGGER.debug(
                        "Unlock tunnel woke after %.1fs (%s tries)",
                        time.monotonic() - started, attempt + 1,
                    )
                return text
            time.sleep(_RETRY_DELAY)
        # Still 2009 -> the device's cloud link is asleep/offline. Re-login won't
        # help, so flag it as a tunnel error rather than an auth failure.
        raise EzvizPrivateError(
            f"device {serial} not reachable via cloud after "
            f"{time.monotonic() - started:.1f}s",
            tunnel=True,
        )

    def _do_unlock(self, serial: str, channel_no: int, door_no: int) -> None:
        client = self._ensure_client()
        api_data = (
            f"PUT /ISAPI/AccessControl/RemoteControl/door/{door_no}\r\n"
            f"<RemoteControlDoor><cmd>open</cmd>"
            f"<channelNo>{channel_no}</channelNo>"
            f"<controlType>monitor</controlType></RemoteControlDoor>"
        )
        text = self._passthrough(client, serial, channel_no, api_data)
        # Success = ISAPI ResponseStatus statusCode 1 (XML) or "1" (JSON).
        if "<statusCode>1<" in text or '"statusCode":"1"' in text:
            return
        raise EzvizPrivateError(f"device rejected unlock: {text[:300]}")

    def unlock(self, serial: str, channel_no: int = 1, door_no: int = 1) -> None:
        """Unlock door ``door_no`` on door-station ``channel_no``.

        Retries once with a fresh login only on a genuine session/auth failure —
        not on a tunnel (2009) error, where re-login would just double the wait.
        """
        started = time.monotonic()
        had_client = self._client is not None
        try:
            self._do_unlock(serial, channel_no, door_no)
        except EzvizPrivateError as err:
            if err.tunnel:
                raise
            _LOGGER.debug("Unlock failed (%s), re-logging in: %s", serial, err)
            self._client = None
            self._do_unlock(serial, channel_no, door_no)
        _LOGGER.debug(
            "Unlock %s ch%s door%s done in %.2fs (session %s)",
            serial, channel_no, door_no, time.monotonic() - started,
            "reused" if had_client else "fresh login",
        )
