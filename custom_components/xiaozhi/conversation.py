"""Xiaozhi AI Conversation Entity for Home Assistant."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import XiaozhiBaseEntity
from .client import XiaozhiWebSocketClient
from .const import DOMAIN, PIPELINE_COLLECT_TIMEOUT
from .models import PipelineCacheManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Xiaozhi conversation entity from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client: XiaozhiWebSocketClient = data["client"]
    cache: PipelineCacheManager = data["cache"]
    async_add_entities([XiaozhiConversationEntity(entry, client, cache)])


class XiaozhiConversationEntity(XiaozhiBaseEntity, ConversationEntity):
    """Xiaozhi AI conversation agent entity."""

    _attr_name = None
    _attr_supported_features = ConversationEntityFeature.CONTROL

    @property
    def supported_languages(self) -> list[str] | str:
        """Return supported languages."""
        return "*"

    def __init__(
        self,
        entry: ConfigEntry,
        client: XiaozhiWebSocketClient,
        cache: PipelineCacheManager,
    ) -> None:
        """Initialize the conversation entity."""
        XiaozhiBaseEntity.__init__(self, entry)
        self._client = client
        self._cache = cache
        self._attr_unique_id = entry.entry_id

    @property
    def available(self) -> bool:
        """Return True if the entity is available."""
        return self._client.is_connected

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Handle a conversation turn."""
        # Voice pipeline mode: STT entity returned immediately and started a
        # background task to collect LLM response + TTS audio.  Wait for it.
        is_voice_mode = False

        collector = await self._cache.get_collector(user_input.text)
        if collector:
            is_voice_mode = True
            _LOGGER.debug("Waiting for pipeline collector: %s", user_input.text)
            if await collector.wait(timeout=PIPELINE_COLLECT_TIMEOUT):
                response_text = collector.response_text
                _LOGGER.debug("Collector ready: %s", response_text)
            elif collector.failed:
                response_text = "Request was replaced by a new voice command."
                _LOGGER.debug("Collector cancelled for: %s", user_input.text)
            else:
                response_text = "Sorry, the request timed out. Please try again."
                _LOGGER.warning("Collector timeout for: %s", user_input.text)
        else:
            # Check instant cache (collector already completed before we got here)
            cached = await self._cache.get_by_input(user_input.text)
            if cached:
                is_voice_mode = True
                _LOGGER.debug("Using cached pipeline response for: %s", user_input.text)
                response_text = cached.response_text
            else:
                # Text mode: normal send_text()
                try:
                    response_text, audio_chunks = await self._client.send_text(
                        user_input.text, language=user_input.language
                    )
                    if audio_chunks:
                        await self._cache.store(user_input.text, response_text, audio_chunks)
                        _LOGGER.debug("Cached %d audio chunks for TTS", len(audio_chunks))
                    else:
                        _LOGGER.debug(
                            "No audio chunks from send_text (server may not send audio for text mode)"
                        )
                except asyncio.TimeoutError:
                    response_text = "Sorry, the request timed out. Please try again."
                    _LOGGER.warning("Xiaozhi response timeout for: %s", user_input.text)
                except ConnectionError as err:
                    response_text = "Sorry, I'm not connected to the Xiaozhi server."
                    _LOGGER.error("Xiaozhi connection error: %s", err)
                except Exception:
                    response_text = "Sorry, an unexpected error occurred."
                    _LOGGER.exception("Unexpected error in Xiaozhi conversation")

        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(
                agent_id=user_input.agent_id,
                content=response_text,
            )
        )

        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(response_text)

        return ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
        )
