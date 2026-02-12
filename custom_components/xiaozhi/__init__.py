"""The Xiaozhi AI Conversation integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import XiaozhiWebSocketClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_MCP_URL,
    CONF_PROTOCOL_VERSION,
    CONF_RESPONSE_TIMEOUT,
    CONF_SERVER_URL,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_RESPONSE_TIMEOUT,
    DOMAIN,
)
from .mcp_client import MCPWebSocketClient
from .mcp_handler import MCPHandler
from .models import PipelineCacheManager, XiaozhiConfig

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION, Platform.STT, Platform.TTS]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Xiaozhi AI Conversation from a config entry."""
    response_timeout = entry.options.get(
        CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT
    )

    config = XiaozhiConfig(
        server_url=entry.data[CONF_SERVER_URL],
        access_token=entry.data.get(CONF_ACCESS_TOKEN, ""),
        device_id=entry.data[CONF_DEVICE_ID],
        client_id=entry.data[CONF_CLIENT_ID],
        protocol_version=entry.data.get(
            CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION
        ),
        response_timeout=response_timeout,
        language=hass.config.language,
    )

    client = XiaozhiWebSocketClient(config)
    mcp_handler = MCPHandler(hass)
    cache = PipelineCacheManager()
    client.set_mcp_handler(mcp_handler)

    try:
        await client.connect()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Could not connect to Xiaozhi server: {err}"
        ) from err

    # Connect MCP WebSocket client if mcp_url is configured
    mcp_url = entry.options.get(CONF_MCP_URL) or entry.data.get(CONF_MCP_URL, "")
    mcp_ws_client: MCPWebSocketClient | None = None
    if mcp_url:
        mcp_ws_client = MCPWebSocketClient(mcp_url, mcp_handler)
        try:
            await mcp_ws_client.connect()
        except Exception:
            _LOGGER.warning("Could not connect to MCP endpoint: %s", mcp_url, exc_info=True)
            # Non-fatal: main WS still works, MCP will reconnect in background

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "mcp_handler": mcp_handler,
        "mcp_client": mcp_ws_client,
        "cache": cache,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        client: XiaozhiWebSocketClient = data["client"]
        await client.disconnect()
        mcp_ws_client: MCPWebSocketClient | None = data.get("mcp_client")
        if mcp_ws_client:
            await mcp_ws_client.disconnect()

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)
