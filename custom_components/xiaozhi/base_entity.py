"""Base entity mixin for Xiaozhi entities.

Provides shared device_info property to avoid duplication across
conversation, stt, and tts entities.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


class XiaozhiBaseEntity:
    """Mixin providing shared device_info for all Xiaozhi entities."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the base entity."""
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for device registry."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="Xiaozhi",
            model="AI Conversation Agent",
        )
