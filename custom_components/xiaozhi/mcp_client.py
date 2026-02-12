"""MCP WebSocket client for separate MCP endpoint communication."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .base_ws import BaseWebSocketClient

if TYPE_CHECKING:
    from .mcp_handler import MCPHandler

_LOGGER = logging.getLogger(__name__)


class MCPWebSocketClient(BaseWebSocketClient):
    """WebSocket client for the separate Xiaozhi MCP endpoint.

    Connects to the MCP WebSocket URL, receives JSON-RPC 2.0 messages
    directly (no {"type":"mcp"} wrapper), routes them through MCPHandler,
    and sends responses back.
    """

    def __init__(
        self,
        url: str,
        mcp_handler: MCPHandler,
    ) -> None:
        """Initialize the MCP WebSocket client."""
        super().__init__()
        self._url = url
        self._mcp_handler = mcp_handler

    def _get_ws_url(self) -> str:
        """Return the MCP WebSocket URL."""
        return self._url

    async def _on_connected(self) -> None:
        """Log connection."""
        _LOGGER.info("MCP WebSocket connected to %s", self._sanitize_url(self._url))

    async def _handle_text_message(self, data: dict[str, Any]) -> None:
        """Handle incoming MCP JSON-RPC message."""
        _LOGGER.debug("MCP received: %s", data.get("method", data.get("id", "?")))

        response = await self._mcp_handler.handle_request(data)
        if response and self._ws:
            await self._ws.send(json.dumps(response))
            _LOGGER.debug("MCP sent response for id=%s", response.get("id"))
