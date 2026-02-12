"""Base WebSocket client with reconnection logic.

Extracts shared connect/reconnect/disconnect/listener patterns used by
both XiaozhiWebSocketClient and MCPWebSocketClient.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl as ssl_module
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.asyncio.client import ClientConnection

from .const import (
    RECONNECT_BACKOFF_FACTOR,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
)

_LOGGER = logging.getLogger(__name__)

# Connection timeout in seconds
_CONNECT_TIMEOUT = 30


class BaseWebSocketClient(ABC):
    """Base WebSocket client with reconnection and SSL support."""

    def __init__(self) -> None:
        """Initialize the base client."""
        self._ws: ClientConnection | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_delay = RECONNECT_MIN_DELAY
        self._should_reconnect = False
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Return True if connected."""
        return self._connected

    @abstractmethod
    def _get_ws_url(self) -> str:
        """Return the WebSocket URL to connect to."""

    def _get_ws_headers(self) -> dict[str, str] | None:
        """Return additional headers for the WebSocket connection."""
        return None

    async def _on_connected(self) -> None:
        """Called after WebSocket connection is established."""

    def _on_disconnected(self) -> None:
        """Called when connection is lost."""

    @abstractmethod
    async def _handle_text_message(self, data: dict[str, Any]) -> None:
        """Handle a parsed JSON text message."""

    async def _handle_binary_message(self, data: bytes) -> None:
        """Handle a binary message. Override if needed."""

    async def connect(self) -> None:
        """Connect to the WebSocket endpoint."""
        self._should_reconnect = True
        await self._connect_once()

    async def _connect_once(self) -> None:
        """Single connection attempt."""
        url = self._get_ws_url()
        headers = self._get_ws_headers()

        # Warn about sending auth over unencrypted connection
        if headers and not url.startswith("wss://"):
            for key, value in headers.items():
                if key.lower() == "authorization" and value:
                    _LOGGER.warning(
                        "Sending auth token over unencrypted ws:// connection to %s",
                        self._sanitize_url(url),
                    )
                    break

        ssl_context = None
        if url.startswith("wss://"):
            loop = asyncio.get_running_loop()
            ssl_context = await loop.run_in_executor(
                None, ssl_module.create_default_context
            )

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    additional_headers=headers,
                    ssl=ssl_context,
                ),
                timeout=_CONNECT_TIMEOUT,
            )
            self._connected = True
            self._reconnect_delay = RECONNECT_MIN_DELAY
            _LOGGER.debug("WebSocket connected to %s", self._sanitize_url(url))

            await self._on_connected()

            self._listener_task = asyncio.get_running_loop().create_task(
                self._listener_loop()
            )

        except Exception:
            self._connected = False
            raise

    async def _listener_loop(self) -> None:
        """Listen for incoming WebSocket messages."""
        assert self._ws is not None

        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    await self._handle_binary_message(message)
                    continue

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    _LOGGER.warning("Received malformed JSON: %s", message[:200])
                    continue

                await self._handle_text_message(data)

        except websockets.ConnectionClosed as exc:
            _LOGGER.warning("WebSocket connection closed: %s", exc)
        except Exception:
            _LOGGER.exception("Error in WebSocket listener")
        finally:
            self._connected = False
            self._on_disconnected()
            if self._should_reconnect:
                self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if self._reconnect_task and not self._reconnect_task.done():
            return

        self._reconnect_task = asyncio.get_running_loop().create_task(
            self._reconnect_loop()
        )

    async def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff."""
        while self._should_reconnect:
            _LOGGER.info(
                "Reconnecting in %s seconds...", self._reconnect_delay
            )
            await asyncio.sleep(self._reconnect_delay)

            try:
                await self._connect_once()
                _LOGGER.info("Reconnected successfully")
                return
            except Exception:
                _LOGGER.warning("Reconnection failed", exc_info=True)
                self._reconnect_delay = min(
                    self._reconnect_delay * RECONNECT_BACKOFF_FACTOR,
                    RECONNECT_MAX_DELAY,
                )

    async def disconnect(self) -> None:
        """Disconnect and stop reconnection attempts."""
        self._should_reconnect = False

        for task in (self._reconnect_task, self._listener_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self._reconnect_task = None
        self._listener_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._connected = False

    @staticmethod
    def _sanitize_url(url: str) -> str:
        """Remove query params from URL for safe logging."""
        parsed = urlparse(url)
        if parsed.query:
            return url[: url.index("?")]
        return url
