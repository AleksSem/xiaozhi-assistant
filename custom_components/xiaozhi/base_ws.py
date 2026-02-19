"""Base WebSocket client with reconnection and keepalive logic.

Provides shared connect/reconnect/disconnect/listener patterns used by
XiaozhiWebSocketClient and MCPWebSocketClient.
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
    KEEPALIVE_INTERVAL,
    KEEPALIVE_TIMEOUT,
    RECONNECT_DELAYS,
)

_LOGGER = logging.getLogger(__name__)

# Connection timeout in seconds
_CONNECT_TIMEOUT = 30


class BaseWebSocketClient(ABC):
    """Base WebSocket client with reconnection, keepalive, and SSL support."""

    def __init__(self) -> None:
        """Initialize the base client."""
        self._ws: ClientConnection | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_step = 0
        self._should_reconnect = False
        self._connected = False

    @property
    def _log_name(self) -> str:
        """Return a human-readable name for log messages."""
        return "WebSocket"

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
                        "%s sending auth token over unencrypted ws:// connection to %s",
                        self._log_name, self._sanitize_url(url),
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
                    ping_interval=None,  # we handle keepalive ourselves
                ),
                timeout=_CONNECT_TIMEOUT,
            )
            self._connected = True
            self._reconnect_step = 0
            _LOGGER.debug("%s connected to %s", self._log_name, self._sanitize_url(url))

            await self._on_connected()

            loop = asyncio.get_running_loop()
            self._listener_task = loop.create_task(self._listener_loop())
            self._keepalive_task = loop.create_task(self._keepalive_loop())

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
                    _LOGGER.warning(
                        "%s received malformed JSON: %s",
                        self._log_name, message[:200],
                    )
                    continue

                await self._handle_text_message(data)

        except websockets.ConnectionClosed as exc:
            _LOGGER.warning("%s connection closed: %s", self._log_name, exc)
        except Exception:
            _LOGGER.exception("Error in %s listener", self._log_name)
        finally:
            self._connected = False
            self._stop_keepalive()
            self._on_disconnected()
            if self._should_reconnect:
                self._schedule_reconnect()

    async def _keepalive_loop(self) -> None:
        """Periodically ping the server to detect dead connections."""
        assert self._ws is not None

        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            try:
                pong = await self._ws.ping()
                await asyncio.wait_for(pong, timeout=KEEPALIVE_TIMEOUT)
                _LOGGER.debug("%s keepalive ok", self._log_name)
            except Exception:
                _LOGGER.warning(
                    "%s keepalive failed, closing connection", self._log_name
                )
                await self._ws.close()
                return

    def _stop_keepalive(self) -> None:
        """Cancel the keepalive task if running."""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt."""
        if self._reconnect_task and not self._reconnect_task.done():
            return

        self._reconnect_task = asyncio.get_running_loop().create_task(
            self._reconnect_loop()
        )

    async def _reconnect_loop(self) -> None:
        """Reconnect with fixed delay schedule."""
        while self._should_reconnect:
            delay = RECONNECT_DELAYS[
                min(self._reconnect_step, len(RECONNECT_DELAYS) - 1)
            ]
            _LOGGER.info(
                "%s reconnecting in %s seconds...",
                self._log_name, delay,
            )
            await asyncio.sleep(delay)

            try:
                await self._connect_once()
                _LOGGER.info("%s reconnected successfully", self._log_name)
                return
            except Exception:
                _LOGGER.warning(
                    "%s reconnection failed", self._log_name, exc_info=True
                )
                self._reconnect_step += 1

    async def disconnect(self) -> None:
        """Disconnect and stop reconnection attempts."""
        self._should_reconnect = False

        for task in (self._reconnect_task, self._listener_task, self._keepalive_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self._reconnect_task = None
        self._listener_task = None
        self._keepalive_task = None

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
