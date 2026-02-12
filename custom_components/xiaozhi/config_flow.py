"""Config flow for Xiaozhi AI Conversation integration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import XiaozhiWebSocketClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_MCP_URL,
    CONF_PROTOCOL_VERSION,
    CONF_RESPONSE_TIMEOUT,
    CONF_SERVER_URL,
    CONNECTION_TYPE_CLOUD,
    CONNECTION_TYPE_SELF_HOSTED,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_RESPONSE_TIMEOUT,
    DOMAIN,
    MAX_RESPONSE_TIMEOUT,
    MIN_RESPONSE_TIMEOUT,
)
from .models import XiaozhiConfig
from .ota import OTAError, XiaozhiOTAClient

_LOGGER = logging.getLogger(__name__)


def _generate_device_id() -> str:
    """Generate a fake MAC-style device ID from UUID."""
    raw = uuid.uuid4().hex[:12]
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


class XiaozhiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xiaozhi AI Conversation."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._device_id: str | None = None
        self._client_id: str | None = None
        self._activation_code: str | None = None
        self._activation_message: str | None = None
        self._ws_url: str | None = None
        self._ws_token: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Choose connection type."""
        if user_input is not None:
            connection_type = user_input[CONF_CONNECTION_TYPE]

            if connection_type == CONNECTION_TYPE_CLOUD:
                return await self.async_step_activate()
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_TYPE, default=CONNECTION_TYPE_CLOUD
                    ): vol.In(
                        {
                            CONNECTION_TYPE_CLOUD: "Xiaozhi Cloud (xiaozhi.me)",
                            CONNECTION_TYPE_SELF_HOSTED: "Self-hosted server",
                        }
                    ),
                }
            ),
        )

    async def async_step_activate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a: Request OTA activation code."""
        self._device_id = _generate_device_id()
        self._client_id = str(uuid.uuid4())

        session = async_get_clientsession(self.hass)
        ota_client = XiaozhiOTAClient(session)

        try:
            result = await ota_client.request_activation(
                self._device_id, self._client_id
            )
        except OTAError as err:
            return self.async_abort(reason="ota_failed", description_placeholders={"error": str(err)})

        if result.is_activated:
            assert result.config is not None  # guaranteed by is_activated
            _LOGGER.debug("OTA returned credentials directly (no activation code needed)")
            return await self._finish_cloud_setup(
                result.config.websocket_url,
                result.config.access_token,
            )

        self._activation_code = result.code
        self._activation_message = result.message
        if result.config:
            self._ws_url = result.config.websocket_url
            self._ws_token = result.config.access_token

        return self.async_show_form(
            step_id="poll",
            description_placeholders={
                "code": self._activation_code or "",
            },
        )

    async def async_step_poll(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b: Poll until activation completes."""
        errors: dict[str, str] = {}

        session = async_get_clientsession(self.hass)
        ota_client = XiaozhiOTAClient(session)

        try:
            assert self._device_id is not None
            assert self._client_id is not None
            ota_config = await ota_client.poll_activation(
                self._device_id,
                self._client_id,
            )
        except OTAError:
            errors["base"] = "activation_timeout"
            return self.async_show_form(
                step_id="poll",
                description_placeholders={
                    "code": self._activation_code or "",
                },
                errors=errors,
            )

        ws_url = ota_config.websocket_url
        ws_token = ota_config.access_token

        # Fall back to credentials from initial OTA call if poll didn't return new ones
        if not ws_url and self._ws_url:
            ws_url = self._ws_url
            ws_token = self._ws_token or ""

        return await self._finish_cloud_setup(ws_url, ws_token)

    async def _validate_connection(self, config: XiaozhiConfig) -> str | None:
        """Validate connection. Returns error key or None."""
        client = XiaozhiWebSocketClient(config)
        try:
            valid = await client.validate_connection()
            if not valid:
                return "cannot_connect"
        except (OSError, asyncio.TimeoutError, ConnectionError):
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error validating connection")
            return "cannot_connect"
        return None

    async def _finish_cloud_setup(
        self, websocket_url: str, access_token: str
    ) -> ConfigFlowResult:
        """Validate cloud connection and create entry."""
        assert self._device_id is not None
        assert self._client_id is not None

        config = XiaozhiConfig(
            server_url=websocket_url,
            access_token=access_token,
            device_id=self._device_id,
            client_id=self._client_id,
        )

        error = await self._validate_connection(config)
        if error:
            return self.async_abort(reason=error)

        return self.async_create_entry(
            title="Xiaozhi AI (Cloud)",
            data={
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_CLOUD,
                CONF_SERVER_URL: websocket_url,
                CONF_ACCESS_TOKEN: access_token,
                CONF_DEVICE_ID: self._device_id,
                CONF_CLIENT_ID: self._client_id,
                CONF_PROTOCOL_VERSION: DEFAULT_PROTOCOL_VERSION,
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2c: Manual self-hosted server setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            server_url = user_input[CONF_SERVER_URL].strip()
            access_token = user_input.get(CONF_ACCESS_TOKEN, "")

            # Validate URL scheme and hostname
            parsed = urlparse(server_url)
            if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
                errors["base"] = "invalid_url"
            else:
                device_id = _generate_device_id()
                client_id = str(uuid.uuid4())

                config = XiaozhiConfig(
                    server_url=server_url,
                    access_token=access_token,
                    device_id=device_id,
                    client_id=client_id,
                )

                error = await self._validate_connection(config)
                if error:
                    errors["base"] = error

                if not errors:
                    return self.async_create_entry(
                        title="Xiaozhi AI (Self-hosted)",
                        data={
                            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SELF_HOSTED,
                            CONF_SERVER_URL: server_url,
                            CONF_ACCESS_TOKEN: access_token,
                            CONF_DEVICE_ID: device_id,
                            CONF_CLIENT_ID: client_id,
                            CONF_PROTOCOL_VERSION: DEFAULT_PROTOCOL_VERSION,
                            CONF_MCP_URL: user_input.get(CONF_MCP_URL, ""),
                        },
                    )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERVER_URL): str,
                    vol.Optional(CONF_ACCESS_TOKEN, default=""): str,
                    vol.Optional(CONF_MCP_URL, default=""): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> XiaozhiOptionsFlow:
        """Get the options flow for this handler."""
        return XiaozhiOptionsFlow()


class XiaozhiOptionsFlow(OptionsFlow):
    """Handle options for Xiaozhi AI Conversation."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_timeout = self.config_entry.options.get(
            CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT
        )
        current_mcp_url = self.config_entry.options.get(
            CONF_MCP_URL,
            self.config_entry.data.get(CONF_MCP_URL, ""),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RESPONSE_TIMEOUT,
                        default=current_timeout,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_RESPONSE_TIMEOUT,
                            max=MAX_RESPONSE_TIMEOUT,
                        ),
                    ),
                    vol.Optional(
                        CONF_MCP_URL,
                        default=current_mcp_url,
                    ): str,
                }
            ),
        )
