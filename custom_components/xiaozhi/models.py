"""Data models for the Xiaozhi AI Conversation integration."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from .const import (
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_RESPONSE_TIMEOUT,
    OTA_DEFAULT_TIMEOUT_MS,
    PIPELINE_CACHE_TTL,
)

_LOGGER = logging.getLogger(__name__)

# Minimum interval between cache cleanup runs (seconds)
_CLEANUP_INTERVAL = 10.0


class ConnectionState(StrEnum):
    """WebSocket connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"


@dataclass
class XiaozhiConfig:
    """Configuration for Xiaozhi client."""

    server_url: str
    access_token: str
    device_id: str
    client_id: str
    protocol_version: int = DEFAULT_PROTOCOL_VERSION
    response_timeout: int = DEFAULT_RESPONSE_TIMEOUT
    language: str | None = None

    def __repr__(self) -> str:
        return (
            f"XiaozhiConfig(server_url={self.server_url!r}, "
            f"device_id={self.device_id!r}, "
            f"protocol_version={self.protocol_version})"
        )


@dataclass
class PendingRequest:
    """A pending text request awaiting response."""

    text: str
    future: asyncio.Future[str]
    response_chunks: list[str] = field(default_factory=list)
    audio_chunks: list[bytes] = field(default_factory=list)
    session_id: str | None = None


@dataclass
class OTAConfig:
    """OTA activation result with WebSocket credentials."""

    websocket_url: str
    access_token: str


@dataclass
class ActivationResult:
    """Result of an OTA activation request.

    Either contains an activation code (device not yet registered)
    or an OTAConfig (device already registered).
    """

    code: str | None = None
    message: str | None = None
    timeout_ms: int = OTA_DEFAULT_TIMEOUT_MS
    config: OTAConfig | None = None

    @property
    def is_activated(self) -> bool:
        """Return True if fully activated (has config, no pending code)."""
        return self.config is not None and self.code is None


@dataclass
class PipelineCache:
    """Cached results from a single Xiaozhi pipeline request."""

    stt_text: str
    response_text: str
    audio_chunks: list[bytes]
    created_at: float = field(default_factory=time.monotonic)


class VoicePipelineSession:
    """Isolated session for a single voice pipeline run.

    Holds all per-request state (callbacks, audio, futures) so concurrent
    voice requests don't overwrite each other's data on the client.
    """

    def __init__(self) -> None:
        """Initialize the voice pipeline session."""
        self.session_id: str = uuid.uuid4().hex
        self.stt_text: str | None = None
        self.stt_event: asyncio.Event = asyncio.Event()
        self.audio_chunks: list[bytes] = []
        self.tts_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.response_chunks: list[str] = []


class PipelineResultCollector:
    """Collects Xiaozhi pipeline results asynchronously.

    Created by STT entity when it returns the STT text immediately.
    The background task continues collecting LLM response + TTS audio
    and signals completion via the ready event.
    Conversation and TTS entities await this event before reading results.
    """

    def __init__(self, stt_text: str) -> None:
        """Initialize the collector."""
        self.stt_text = stt_text
        self.ready = asyncio.Event()
        self.response_text: str | None = None
        self.audio_chunks: list[bytes] = []
        self.created_at = time.monotonic()

    def complete(self, response_text: str, audio_chunks: list[bytes]) -> None:
        """Mark collection as complete with results."""
        self.response_text = response_text
        self.audio_chunks = audio_chunks
        self.ready.set()

    def fail(self) -> None:
        """Mark collection as failed (no results)."""
        self.ready.set()

    async def wait(self, timeout: float = 60) -> bool:
        """Wait for results to be ready. Returns True if successful."""
        try:
            await asyncio.wait_for(self.ready.wait(), timeout)
            return self.response_text is not None
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for pipeline results (stt=%s)", self.stt_text)
            return False


class PipelineCacheManager:
    """Manages cached pipeline results with TTL.

    In voice mode, STT entity returns immediately and creates a collector.
    The background task fills the collector with LLM response + TTS audio.
    Conversation and TTS entities await the collector before reading results.

    Uses async lock for thread safety and deque-based collectors to handle
    duplicate STT text within the same TTL window (FIFO order).
    """

    def __init__(self, ttl: float = PIPELINE_CACHE_TTL) -> None:
        """Initialize the cache manager."""
        self._cache: dict[str, PipelineCache] = {}
        self._response_index: dict[str, str] = {}
        self._collectors: dict[str, deque[PipelineResultCollector]] = {}
        self._ttl = ttl
        self._lock = asyncio.Lock()
        self._last_cleanup = 0.0

    async def create_collector(self, stt_text: str) -> PipelineResultCollector:
        """Create a result collector for a voice pipeline run."""
        async with self._lock:
            self._cleanup_if_needed()
            collector = PipelineResultCollector(stt_text)
            if stt_text not in self._collectors:
                self._collectors[stt_text] = deque()
            self._collectors[stt_text].append(collector)
            return collector

    async def get_collector(self, stt_text: str) -> PipelineResultCollector | None:
        """Get the oldest active collector by STT input text (FIFO)."""
        async with self._lock:
            q = self._collectors.get(stt_text)
            if q:
                return q[0]
            return None

    async def complete_collector(
        self,
        stt_text: str,
        response_text: str,
        audio_chunks: list[bytes],
    ) -> None:
        """Complete the oldest collector and store results in cache."""
        async with self._lock:
            q = self._collectors.get(stt_text)
            if q:
                collector = q.popleft()
                collector.complete(response_text, audio_chunks)
                if not q:
                    del self._collectors[stt_text]
            self._store_locked(stt_text, response_text, audio_chunks)

    async def fail_collector(self, stt_text: str) -> None:
        """Mark the oldest collector as failed."""
        async with self._lock:
            q = self._collectors.get(stt_text)
            if q:
                collector = q.popleft()
                collector.fail()
                if not q:
                    del self._collectors[stt_text]
            # Clean up any partial response index entries for this stt_text
            keys_to_remove = [k for k, v in self._response_index.items() if v == stt_text]
            for k in keys_to_remove:
                del self._response_index[k]

    async def store(
        self,
        stt_text: str,
        response_text: str,
        audio_chunks: list[bytes],
    ) -> None:
        """Store pipeline results keyed by STT text."""
        async with self._lock:
            self._cleanup_if_needed()
            self._store_locked(stt_text, response_text, audio_chunks)

    async def get_by_input(self, stt_text: str) -> PipelineCache | None:
        """Look up cached results by STT input text."""
        async with self._lock:
            self._cleanup_if_needed()
            entry = self._cache.pop(stt_text, None)
            if entry:
                self._response_index.pop(entry.response_text, None)
            return entry

    async def get_audio_by_response(self, response_text: str) -> list[bytes] | None:
        """Look up cached audio by LLM response text (for TTS entity)."""
        async with self._lock:
            self._cleanup_if_needed()
            stt_text = self._response_index.pop(response_text, None)
            if stt_text is None:
                return None
            entry = self._cache.pop(stt_text, None)
            if entry is None:
                return None
            return entry.audio_chunks

    def _store_locked(
        self,
        stt_text: str,
        response_text: str,
        audio_chunks: list[bytes],
    ) -> None:
        """Store pipeline results (must be called under lock)."""
        entry = PipelineCache(
            stt_text=stt_text,
            response_text=response_text,
            audio_chunks=audio_chunks,
        )
        self._cache[stt_text] = entry
        self._response_index[response_text] = stt_text

    def _cleanup_if_needed(self) -> None:
        """Remove expired entries if enough time has passed since last cleanup."""
        now = time.monotonic()
        if now - self._last_cleanup < _CLEANUP_INTERVAL:
            return
        self._last_cleanup = now

        expired = [
            k for k, v in self._cache.items()
            if now - v.created_at > self._ttl
        ]
        for k in expired:
            entry = self._cache.pop(k)
            self._response_index.pop(entry.response_text, None)
        # Clean expired collectors
        expired_keys: list[str] = []
        for k, q in self._collectors.items():
            while q and now - q[0].created_at > self._ttl:
                q[0].fail()
                q.popleft()
            if not q:
                expired_keys.append(k)
        for k in expired_keys:
            del self._collectors[k]
