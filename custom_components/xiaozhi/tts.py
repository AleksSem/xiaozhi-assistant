"""Xiaozhi TTS Entity for Home Assistant.

Returns cached audio from the voice pipeline. When the STT entity processes
a voice request, it captures the full pipeline output including TTS audio.
This entity serves that cached audio back to HA's pipeline.
"""

from __future__ import annotations

import logging

from homeassistant.components.tts import TextToSpeechEntity, TtsAudioType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .audio import generate_silence_wav, opus_frames_to_wav
from .base_entity import XiaozhiBaseEntity
from .const import DOMAIN, PIPELINE_COLLECT_TIMEOUT, SUPPORTED_LANGUAGES
from .models import PipelineCacheManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Xiaozhi TTS entity from a config entry."""
    cache: PipelineCacheManager = hass.data[DOMAIN][entry.entry_id]["cache"]
    async_add_entities([XiaozhiTTSEntity(entry, cache)])


class XiaozhiTTSEntity(XiaozhiBaseEntity, TextToSpeechEntity):
    """Text-to-speech using cached Xiaozhi audio."""

    _attr_name = "TTS"

    @property
    def supported_languages(self) -> list[str]:
        """Return supported languages."""
        return SUPPORTED_LANGUAGES

    @property
    def default_language(self) -> str:
        """Return the default language."""
        return "zh"

    def __init__(
        self,
        entry: ConfigEntry,
        cache: PipelineCacheManager,
    ) -> None:
        """Initialize the TTS entity."""
        XiaozhiBaseEntity.__init__(self, entry)
        self._cache = cache
        self._attr_unique_id = f"{entry.entry_id}_tts"

    async def async_get_tts_audio(
        self,
        message: str,
        language: str,
        options: dict | None = None,
    ) -> TtsAudioType:
        """Return TTS audio from pipeline cache.

        In voice pipeline mode, audio is already cached by the STT entity.
        This avoids a second request to Xiaozhi server.
        If no audio is available, returns silence to prevent pipeline errors.
        """
        # Wait for async collector if the background task hasn't finished yet
        collector = await self._cache.get_collector(message)
        if collector:
            _LOGGER.debug("TTS waiting for pipeline collector: %.50s...", message)
            if not await collector.wait(timeout=PIPELINE_COLLECT_TIMEOUT):
                _LOGGER.warning("TTS collector timeout: %.50s...", message)
                return ("wav", generate_silence_wav())
        else:
            _LOGGER.debug(
                "TTS no collector (fast-server path): %.50s...", message
            )

        _LOGGER.debug("TTS looking up cache for response: %.80s...", message)
        audio_chunks = await self._cache.get_audio_by_response(message)
        if audio_chunks:
            _LOGGER.debug("Serving cached TTS audio (%d chunks)", len(audio_chunks))
            wav_data = await opus_frames_to_wav(audio_chunks)
            if wav_data is not None and wav_data:
                return ("wav", wav_data)
            _LOGGER.warning("Failed to decode cached opus audio to WAV")

        # Fallback: return silence instead of (None, None) to avoid
        # InvalidStateError in HA's TTS pipeline
        _LOGGER.debug(
            "No cached audio for response, returning silence: %.50s...",
            message,
        )
        return ("wav", generate_silence_wav())
