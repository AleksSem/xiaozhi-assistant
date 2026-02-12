"""OTA activation client for Xiaozhi cloud."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from .const import (
    APP_VERSION,
    OTA_BOARD_NAME,
    OTA_BOARD_TYPE,
    OTA_DEFAULT_TIMEOUT_MS,
    OTA_POLL_INTERVAL,
    OTA_TIMEOUT,
    OTA_URL,
)
from .models import ActivationResult, OTAConfig

_LOGGER = logging.getLogger(__name__)


class OTAError(Exception):
    """OTA activation error."""


class XiaozhiOTAClient:
    """Handles device registration with Xiaozhi cloud via OTA endpoint."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        ota_url: str = OTA_URL,
    ) -> None:
        """Initialize the OTA client."""
        self._session = session
        self._ota_url = ota_url

    async def request_activation(
        self,
        device_id: str,
        client_id: str,
    ) -> ActivationResult:
        """Request OTA activation.

        Returns ActivationResult with either:
        - code/message (device not registered, user must enter code on xiaozhi.me)
        - config (device already registered, ready to connect)
        """
        headers = {
            "Device-Id": device_id,
            "Client-Id": client_id,
            "Activation-Version": "1",
            "Content-Type": "application/json",
        }

        body = {
            "application": {
                "version": APP_VERSION,
            },
            "board": {
                "type": OTA_BOARD_TYPE,
                "name": OTA_BOARD_NAME,
                "mac": device_id,
            },
        }

        try:
            async with self._session.post(
                self._ota_url, headers=headers, json=body
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise OTAError(
                        f"OTA request failed: HTTP {resp.status} ({body[:200]})"
                    )
                try:
                    data = await resp.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise OTAError(f"Invalid OTA response: {err}") from err
        except aiohttp.ClientError as err:
            raise OTAError(f"OTA request failed: {err}") from err

        return self._parse_response(data)

    async def poll_activation(
        self,
        device_id: str,
        client_id: str,
        interval: float = OTA_POLL_INTERVAL,
        timeout: float = OTA_TIMEOUT,
    ) -> OTAConfig:
        """Poll OTA endpoint until device is activated.

        Raises OTAError if timeout expires.
        """
        elapsed = 0.0

        while elapsed < timeout:
            result = await self.request_activation(device_id, client_id)

            if result.is_activated:
                return result.config  # type: ignore[return-value]

            await asyncio.sleep(interval)
            elapsed += interval

        raise OTAError("Activation timed out")

    @staticmethod
    def _parse_response(data: dict) -> ActivationResult:
        """Parse OTA response into ActivationResult."""
        safe = {k: v for k, v in data.items() if k != "websocket"}
        _LOGGER.debug("OTA response: %s", safe)

        if not isinstance(data, dict):
            raise OTAError(f"Invalid OTA response type: {type(data).__name__}")

        ws_info = data.get("websocket", {})
        activation = data.get("activation", {})
        code = activation.get("code")

        # Build OTAConfig from websocket info if available
        ota_config = None
        ws_url = ws_info.get("url", "") if isinstance(ws_info, dict) else ""
        ws_token = ws_info.get("token", "") if isinstance(ws_info, dict) else ""
        if ws_url and ws_token:
            if not isinstance(ws_url, str) or not isinstance(ws_token, str):
                raise OTAError("Invalid websocket credentials in OTA response")
            ota_config = OTAConfig(
                websocket_url=ws_url,
                access_token=ws_token,
            )

        # If activation code present — device needs activation (even if websocket present)
        if code:
            code_str = str(code)
            # Validate activation code format (expected: 6 digits)
            if not code_str.isdigit() or len(code_str) != 6:
                _LOGGER.warning("Unexpected activation code format: %s", code_str)

            timeout_ms = activation.get("timeout_ms", OTA_DEFAULT_TIMEOUT_MS)
            if not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0:
                timeout_ms = OTA_DEFAULT_TIMEOUT_MS

            return ActivationResult(
                code=code_str,
                message=activation.get("message", ""),
                timeout_ms=int(timeout_ms),
                config=ota_config,
            )

        # No activation code — fully activated
        if ota_config:
            return ActivationResult(config=ota_config)

        raise OTAError(f"Unexpected OTA response: {data}")
