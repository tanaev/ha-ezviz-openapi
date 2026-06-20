"""Async client for the EZVIZ Open Platform API.

Only the few endpoints needed to enumerate devices/channels and obtain a fresh,
playable live-stream URL (HLS/RTMP/FLV). Auth is appKey+appSecret -> accessToken.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

_SUCCESS = "200"
_TOKEN_EXPIRED = "10002"
# Codes that mean the credentials themselves are wrong -> trigger reauth.
_AUTH_ERROR_CODES = {"10001", "10005", "10013", "10014", "10017", "10030", "10031"}
_TIMEOUT = aiohttp.ClientTimeout(total=20)


class EzvizApiError(Exception):
    """Generic EZVIZ Open API error."""

    def __init__(self, code: str, msg: str) -> None:
        self.code = code
        self.msg = msg
        super().__init__(f"EZVIZ API error {code}: {msg}")


class EzvizAuthError(EzvizApiError):
    """Invalid appKey/appSecret — the integration must be reconfigured."""


class EzvizOpenApi:
    """Thin async wrapper over the EZVIZ Open API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        app_key: str,
        app_secret: str,
        base_url: str,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._verify_ssl = verify_ssl
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # -- low level ---------------------------------------------------------
    def _kwargs(self, **extra: Any) -> dict[str, Any]:
        kw: dict[str, Any] = {"timeout": _TIMEOUT, **extra}
        if not self._verify_ssl:
            kw["ssl"] = False
        return kw

    async def _raw_post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        async with self._session.post(
            f"{self._base_url}{path}", **self._kwargs(data=data)
        ) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _post(
        self, path: str, data: dict[str, Any], _retry: bool = True
    ) -> Any:
        body = await self._raw_post(path, data)
        code = str(body.get("code"))
        if code == _SUCCESS:
            return body.get("data")
        if code == _TOKEN_EXPIRED and _retry:
            token = await self.async_get_token(force=True)
            return await self._post(path, {**data, "accessToken": token}, _retry=False)
        if code in _AUTH_ERROR_CODES:
            raise EzvizAuthError(code, body.get("msg", ""))
        raise EzvizApiError(code, body.get("msg", ""))

    # -- auth --------------------------------------------------------------
    async def async_get_token(self, force: bool = False) -> str:
        now = time.time()
        if self._token and not force and now < self._token_expiry - 300:
            return self._token
        body = await self._raw_post(
            "/api/lapp/token/get",
            {"appKey": self._app_key, "appSecret": self._app_secret},
        )
        code = str(body.get("code"))
        if code != _SUCCESS:
            if code in _AUTH_ERROR_CODES:
                raise EzvizAuthError(code, body.get("msg", ""))
            raise EzvizApiError(code, body.get("msg", ""))
        data = body["data"]
        self._token = data["accessToken"]
        # expireTime is epoch millis; fall back to ~6 days if absent.
        self._token_expiry = (int(data.get("expireTime", 0)) / 1000) or (now + 6 * 86400)
        return self._token

    async def _auth_post(self, path: str, extra: dict[str, Any]) -> Any:
        token = await self.async_get_token()
        return await self._post(path, {"accessToken": token, **extra})

    # -- endpoints ---------------------------------------------------------
    async def async_device_list(self, page_size: int = 50) -> list[dict[str, Any]]:
        data = await self._auth_post(
            "/api/lapp/device/list", {"pageStart": 0, "pageSize": page_size}
        )
        return data or []

    async def async_camera_list(self, page_size: int = 50) -> list[dict[str, Any]]:
        data = await self._auth_post(
            "/api/lapp/camera/list", {"pageStart": 0, "pageSize": page_size}
        )
        return data or []

    async def async_live_address(
        self,
        serial: str,
        channel: int = 1,
        protocol: int = 2,
        code: str | None = None,
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "deviceSerial": serial,
            "channelNo": channel,
            "protocol": protocol,
        }
        if code:
            extra["code"] = code  # verify code for encrypted devices
        return await self._auth_post("/api/lapp/live/address/get", extra) or {}

    async def async_capture(self, serial: str, channel: int = 1) -> str | None:
        """Trigger a snapshot, return its picture URL (for thumbnails)."""
        data = await self._auth_post(
            "/api/lapp/device/capture",
            {"deviceSerial": serial, "channelNo": channel},
        )
        return (data or {}).get("picUrl")

    async def async_fetch_image(self, url: str) -> bytes:
        async with self._session.get(url, **self._kwargs()) as resp:
            resp.raise_for_status()
            return await resp.read()
