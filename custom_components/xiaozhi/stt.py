"""Xiaozhi STT Entity for Home Assistant.

Streams audio to Xiaozhi server via WebSocket, captures the STT result
immediately, and starts a background task to collect LLM response + TTS audio.

Key design: STT returns text ASAP (non-blocking) so the HA pipeline can
proceed to Conversation and TTS stages without delay. The background task
fills the PipelineResultCollector, which Conversation and TTS entities await.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable

from homeassistant.components.stt import (
    AudioBitRates,
    AudioChannels,
    AudioCodecs,
    AudioFormats,
    AudioSampleRates,
    SpeechMetadata,
    SpeechResult,
    SpeechResultState,
    SpeechToTextEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .audio import pcm_to_opus_frames
from .base_entity import XiaozhiBaseEntity
from .client import XiaozhiWebSocketClient
from .const import DOMAIN, PIPELINE_COLLECT_TIMEOUT, STT_RESULT_TIMEOUT, SUPPORTED_LANGUAGES
from .models import PipelineCacheManager, VoicePipelineSession

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Xiaozhi STT entity from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client: XiaozhiWebSocketClient = data["client"]
    cache: PipelineCacheManager = data["cache"]
    async_add_entities([XiaozhiSTTEntity(entry, client, cache)])


class XiaozhiSTTEntity(XiaozhiBaseEntity, SpeechToTextEntity):
    """Speech-to-text using Xiaozhi cloud.

    Streams audio to the Xiaozhi WebSocket, which runs the full pipeline
    (STT → LLM → TTS). Returns STT text immediately and collects
    LLM response + TTS audio in a background task.
    """

    _attr_name = "STT"

    def __init__(
        self,
        entry: ConfigEntry,
        client: XiaozhiWebSocketClient,
        cache: PipelineCacheManager,
    ) -> None:
        """Initialize the STT entity."""
        XiaozhiBaseEntity.__init__(self, entry)
        self._client = client
        self._cache = cache
        self._attr_unique_id = f"{entry.entry_id}_stt"

    @property
    def supported_languages(self) -> list[str]:
        """Return supported languages."""
        return SUPPORTED_LANGUAGES

    @property
    def supported_codecs(self) -> list[AudioCodecs]:
        """Return supported audio codecs."""
        return [AudioCodecs.PCM]

    @property
    def supported_formats(self) -> list[AudioFormats]:
        """Return supported audio formats."""
        return [AudioFormats.WAV]

    @property
    def supported_sample_rates(self) -> list[AudioSampleRates]:
        """Return supported sample rates."""
        return [AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[AudioChannels]:
        """Return supported audio channels."""
        return [AudioChannels.CHANNEL_MONO]

    @property
    def supported_bit_rates(self) -> list[AudioBitRates]:
        """Return supported bit rates."""
        return [AudioBitRates.BITRATE_16]

    @property
    def available(self) -> bool:
        """Return True if the entity is available."""
        return self._client.is_connected

    async def async_process_audio_stream(
        self,
        metadata: SpeechMetadata,
        stream: AsyncIterable[bytes],
    ) -> SpeechResult:
        """Process audio stream through Xiaozhi pipeline.

        1. Stream PCM audio → convert to opus → send as binary WS frames
        2. Wait for STT result from server (fast: 1-5 sec after audio ends)
        3. Start background task to collect LLM response + TTS audio
        4. Return STT text immediately (non-blocking)
        """
        if not self._client.is_connected:
            _LOGGER.warning("STT unavailable: not connected to Xiaozhi server")
            return SpeechResult(text=None, result=SpeechResultState.ERROR)

        # Create an isolated voice session for this pipeline run
        session = VoicePipelineSession()
        self._client.register_voice_session(session)

        try:
            # Signal Xiaozhi to start listening for audio
            await self._client.start_listening(language=metadata.language)

            # Stream audio: PCM → opus → binary frames → WebSocket
            frame_count = 0
            async for opus_frame in pcm_to_opus_frames(stream.__aiter__()):
                await self._client.send_audio_frame(opus_frame)
                frame_count += 1

            _LOGGER.debug("Audio streaming complete: sent %d opus frames", frame_count)

            # Tell server we're done sending audio
            await self._client.stop_listening()

            # Wait for STT result (should be fast: 1-5 sec)
            try:
                await asyncio.wait_for(session.stt_event.wait(), timeout=STT_RESULT_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout waiting for STT result")
                self._client.unregister_voice_session(session.session_id)
                return SpeechResult(text=None, result=SpeechResultState.ERROR)

            stt_text = session.stt_text
            if not stt_text:
                self._client.unregister_voice_session(session.session_id)
                return SpeechResult(text=None, result=SpeechResultState.ERROR)

            # Create collector for Conversation + TTS entities to await
            await self._cache.create_collector(stt_text)

            # Start background task to collect LLM response + TTS audio
            asyncio.create_task(
                self._collect_pipeline_results(session, stt_text)
            )

            # Return STT text immediately — don't block the pipeline
            _LOGGER.debug("STT result: %s (pipeline collecting in background)", stt_text)
            return SpeechResult(text=stt_text, result=SpeechResultState.SUCCESS)

        except Exception:
            _LOGGER.exception("Error during STT processing")
            self._client.unregister_voice_session(session.session_id)
            # Cancel the TTS future if not yet done
            if not session.tts_future.done():
                session.tts_future.cancel()
            return SpeechResult(text=None, result=SpeechResultState.ERROR)

    async def _collect_pipeline_results(
        self,
        session: VoicePipelineSession,
        stt_text: str,
    ) -> None:
        """Background task: collect LLM response + TTS audio from Xiaozhi."""
        try:
            response_text = await asyncio.wait_for(
                session.tts_future, timeout=PIPELINE_COLLECT_TIMEOUT
            )

            # Store in cache and signal collector
            await self._cache.complete_collector(
                stt_text, response_text, list(session.audio_chunks)
            )
            _LOGGER.debug(
                "Pipeline collected: stt=%s, response=%.50s..., audio=%d chunks",
                stt_text, response_text, len(session.audio_chunks),
            )

        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout collecting pipeline results for: %s", stt_text)
            await self._cache.fail_collector(stt_text)
        except asyncio.CancelledError:
            _LOGGER.debug("Pipeline collection cancelled for: %s", stt_text)
            await self._cache.fail_collector(stt_text)
        except Exception:
            _LOGGER.exception("Error collecting pipeline results")
            await self._cache.fail_collector(stt_text)
        finally:
            self._client.unregister_voice_session(session.session_id)
