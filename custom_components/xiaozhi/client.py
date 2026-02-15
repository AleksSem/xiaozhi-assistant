"""WebSocket client for Xiaozhi server communication."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from .audio import pack_audio_frame, unpack_audio_frame
from .base_ws import BaseWebSocketClient
from .const import (
    LISTEN_STATE_DETECT,
    LISTEN_STATE_START,
    LISTEN_STATE_STOP,
    MSG_TYPE_HELLO,
    MSG_TYPE_LISTEN,
    MSG_TYPE_MCP,
    MSG_TYPE_STT,
    MSG_TYPE_TTS,
    TTS_STATE_SENTENCE_START,
    TTS_STATE_START,
    TTS_STATE_STOP,
)
from .models import ConnectionState, PendingRequest, VoicePipelineSession, XiaozhiConfig

if TYPE_CHECKING:
    from .mcp_handler import MCPHandler

_LOGGER = logging.getLogger(__name__)


class XiaozhiWebSocketClient(BaseWebSocketClient):
    """Persistent WebSocket client for the Xiaozhi server."""

    def __init__(self, config: XiaozhiConfig) -> None:
        """Initialize the client."""
        super().__init__()
        self._config = config
        self._state = ConnectionState.DISCONNECTED
        self._session_id: str | None = None
        self._pending: PendingRequest | None = None
        self._send_lock = asyncio.Lock()
        self._tts_done = asyncio.Event()
        self._tts_done.set()  # clean state, no drain needed initially
        self._mcp_handler: MCPHandler | None = None
        # Voice pipeline sessions (replaces global _stt_callback/_audio_callback)
        self._active_voice_session: VoicePipelineSession | None = None

    @property
    def state(self) -> ConnectionState:
        """Return the current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Return True if authenticated and ready."""
        return self._state == ConnectionState.AUTHENTICATED

    def _get_ws_url(self) -> str:
        """Return the WebSocket URL."""
        return self._config.server_url

    def _get_ws_headers(self) -> dict[str, str]:
        """Return connection headers with auth and device info."""
        return {
            "Authorization": f"Bearer {self._config.access_token}",
            "Protocol-Version": str(self._config.protocol_version),
            "Device-Id": self._config.device_id,
            "Client-Id": self._config.client_id,
        }

    async def _on_connected(self) -> None:
        """Perform hello handshake after connection."""
        self._state = ConnectionState.CONNECTED
        self._tts_done.set()  # clean state after reconnect
        await self._hello_handshake()

    def _on_disconnected(self) -> None:
        """Handle disconnection: fail pending requests."""
        self._state = ConnectionState.DISCONNECTED
        self._fail_pending("Connection lost")

    async def _connect_once(self) -> None:
        """Single connection attempt with hello handshake."""
        self._state = ConnectionState.CONNECTING
        try:
            await super()._connect_once()
        except Exception:
            self._state = ConnectionState.DISCONNECTED
            raise

    def set_mcp_handler(self, handler: MCPHandler) -> None:
        """Set the MCP handler for tool call routing."""
        self._mcp_handler = handler

    def register_voice_session(self, session: VoicePipelineSession) -> None:
        """Register a voice pipeline session as active.

        Only one voice session can be active at a time. The session receives
        STT results, TTS chunks, and audio frames from the server.
        If a previous session is still active, it gets cancelled with a warning.
        """
        old = self._active_voice_session
        if old is not None:
            _LOGGER.debug(
                "Overwriting active voice session %s with %s",
                old.session_id, session.session_id,
            )
            if not old.tts_future.done():
                old.tts_future.cancel()
        self._active_voice_session = session

    def unregister_voice_session(self, session_id: str) -> None:
        """Unregister a voice pipeline session.

        Only unregisters if the given session_id matches the active session,
        preventing a late cleanup from clobbering a newer session.
        """
        if (
            self._active_voice_session is not None
            and self._active_voice_session.session_id == session_id
        ):
            self._active_voice_session = None

    async def send_audio_frame(self, opus_data: bytes) -> None:
        """Send an opus audio frame as a binary WebSocket message."""
        if not self.is_connected or self._ws is None:
            raise ConnectionError("Not connected to Xiaozhi server")
        frame = pack_audio_frame(opus_data)
        await self._ws.send(frame)

    async def start_listening(self, language: str | None = None) -> None:
        """Send listen start command to begin audio streaming."""
        if not self.is_connected or self._ws is None:
            raise ConnectionError("Not connected to Xiaozhi server")
        msg: dict[str, Any] = {"type": MSG_TYPE_LISTEN, "state": LISTEN_STATE_START}
        if language:
            msg["language"] = language
        await self._ws.send(json.dumps(msg))
        _LOGGER.debug("Sent listen start (language=%s)", language)

    async def stop_listening(self) -> None:
        """Send listen stop command to end audio streaming."""
        if not self.is_connected or self._ws is None:
            raise ConnectionError("Not connected to Xiaozhi server")
        msg = {"type": MSG_TYPE_LISTEN, "state": LISTEN_STATE_STOP}
        await self._ws.send(json.dumps(msg))
        _LOGGER.debug("Sent listen stop")

    async def _hello_handshake(self) -> None:
        """Send hello and wait for server hello response."""
        assert self._ws is not None

        hello_msg: dict[str, Any] = {
            "type": MSG_TYPE_HELLO,
            "version": 1,
            "transport": "websocket",
            "features": {"mcp": True},
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
            },
        }
        if self._config.language:
            hello_msg["audio_params"]["language"] = self._config.language

        await self._ws.send(json.dumps(hello_msg))
        _LOGGER.debug("Sent hello message")

        response = await asyncio.wait_for(self._ws.recv(), timeout=10)
        if isinstance(response, bytes):
            raise ConnectionError("Expected text hello response, got binary")

        data = json.loads(response)
        if data.get("type") != MSG_TYPE_HELLO:
            raise ConnectionError(f"Expected hello response, got: {data.get('type')}")

        self._session_id = data.get("session_id")
        if not self._session_id:
            _LOGGER.warning("Server hello without session_id")
        self._state = ConnectionState.AUTHENTICATED
        _LOGGER.debug("Authenticated, session_id=%s", self._session_id)

    async def send_text(
        self, text: str, language: str | None = None
    ) -> tuple[str, list[bytes]]:
        """Send text to Xiaozhi server and wait for the response.

        Returns (response_text, audio_chunks).
        Raises asyncio.TimeoutError if response takes too long.

        Requests are serialized via _send_lock. If a previous request timed
        out, the server's leftover TTS stream is drained before sending.
        """
        if not self.is_connected:
            raise ConnectionError("Not connected to Xiaozhi server")

        async with self._send_lock:
            # Drain any leftover TTS from a previous timed-out request
            if not self._tts_done.is_set():
                _LOGGER.debug("Draining stale TTS before sending: %s", text)
                try:
                    await asyncio.wait_for(self._tts_done.wait(), timeout=5)
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Drain timeout — server may not have finished previous request"
                    )

            self._tts_done.clear()

            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            self._pending = PendingRequest(
                text=text,
                future=future,
                session_id=self._session_id,
            )

            msg: dict[str, Any] = {
                "type": MSG_TYPE_LISTEN,
                "state": LISTEN_STATE_DETECT,
                "text": text,
            }
            if language:
                msg["language"] = language

            assert self._ws is not None
            await self._ws.send(json.dumps(msg))
            _LOGGER.debug("Sent text: %s", text)

            try:
                result_text = await asyncio.wait_for(
                    future, timeout=self._config.response_timeout
                )
                # Yield to event loop — let listener process any trailing binary frames
                await asyncio.sleep(0)
                audio = list(self._pending.audio_chunks) if self._pending else []
                _LOGGER.debug(
                    "send_text result: text=%.50s..., audio_chunks=%d",
                    result_text, len(audio),
                )
                return result_text, audio
            except asyncio.TimeoutError:
                self._pending = None
                # Wait for server to finish its TTS stream before next request
                _LOGGER.debug("Timeout — waiting for server to finish TTS")
                try:
                    await asyncio.wait_for(self._tts_done.wait(), timeout=5)
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Server did not finish TTS within drain timeout"
                    )
                raise
            finally:
                if self._pending and self._pending.future is future:
                    self._pending = None

    async def _handle_binary_message(self, data: bytes) -> None:
        """Handle incoming binary WebSocket frame (audio)."""
        opus_payload = unpack_audio_frame(data)
        if opus_payload is None:
            return

        # Route to active voice session
        session = self._active_voice_session
        if session is not None:
            session.audio_chunks.append(opus_payload)

        # Also collect for text-mode pending request
        if self._pending:
            self._pending.audio_chunks.append(opus_payload)

    async def _handle_text_message(self, data: dict[str, Any]) -> None:
        """Route incoming message by type."""
        msg_type = data.get("type")

        if msg_type == MSG_TYPE_TTS:
            self._handle_tts(data)
        elif msg_type == MSG_TYPE_STT:
            self._handle_stt(data)
        elif msg_type == MSG_TYPE_MCP:
            await self._handle_mcp(data)
        elif msg_type == MSG_TYPE_HELLO:
            # Server re-hello, update session
            self._session_id = data.get("session_id")
        else:
            _LOGGER.debug("Received message type=%s", msg_type)

    def _handle_tts(self, data: dict[str, Any]) -> None:
        """Handle TTS messages, collecting sentence chunks."""
        state = data.get("state")
        session = self._active_voice_session

        if state == TTS_STATE_START:
            _LOGGER.debug("TTS stream started")
        elif state == TTS_STATE_SENTENCE_START:
            text = data.get("text", "")
            if text and not text.startswith("%"):
                if session is not None:
                    session.response_chunks.append(text)
                if self._pending:
                    self._pending.response_chunks.append(text)
            _LOGGER.debug("TTS chunk: %s", text)
        elif state == TTS_STATE_STOP:
            _LOGGER.debug("TTS stream stopped")
            self._tts_done.set()
            if session is not None and not session.tts_future.done():
                result = " ".join(session.response_chunks)
                session.tts_future.set_result(result)
            if self._pending and not self._pending.future.done():
                result = " ".join(self._pending.response_chunks)
                self._pending.future.set_result(result)

    def _handle_stt(self, data: dict[str, Any]) -> None:
        """Handle STT result message from server."""
        text = data.get("text", "")
        if not text:
            return

        session = self._active_voice_session
        if session is not None:
            session.stt_text = text
            session.stt_event.set()
        _LOGGER.debug("STT result: %s", text)

    async def _handle_mcp(self, data: dict[str, Any]) -> None:
        """Handle incoming MCP tool call requests."""
        if self._mcp_handler is None:
            _LOGGER.warning("Received MCP message but no handler configured")
            return

        mcp_data = data.get("payload", {})
        response = await self._mcp_handler.handle_request(mcp_data)

        if response and self._ws:
            msg = {"type": MSG_TYPE_MCP, "payload": response}
            await self._ws.send(json.dumps(msg))

    def _fail_pending(self, reason: str) -> None:
        """Fail any pending request with an error."""
        if self._pending and not self._pending.future.done():
            self._pending.future.set_exception(ConnectionError(reason))
        # Also fail active voice session
        session = self._active_voice_session
        if session is not None and not session.tts_future.done():
            session.tts_future.set_exception(ConnectionError(reason))
            session.stt_event.set()

    async def disconnect(self) -> None:
        """Disconnect and stop reconnection attempts."""
        await super().disconnect()
        self._state = ConnectionState.DISCONNECTED
        self._fail_pending("Client disconnected")

    async def validate_connection(self) -> bool:
        """Test connection for config flow validation.

        Connects, performs hello, then disconnects.
        Returns True if successful.
        """
        try:
            await self._connect_once()
            return True
        finally:
            self._should_reconnect = False
            if self._listener_task and not self._listener_task.done():
                self._listener_task.cancel()
            if self._ws:
                await self._ws.close()
                self._ws = None
            self._state = ConnectionState.DISCONNECTED
