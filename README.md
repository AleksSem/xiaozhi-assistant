# Xiaozhi AI Conversation for Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.7%2B-blue.svg)](https://www.home-assistant.io/)
[![Sponsor](https://img.shields.io/badge/Sponsor-PayPal-blue.svg?logo=paypal)](https://www.paypal.com/donate/?business=96G47VVQMMLFW&no_recurring=0&currency_code=EUR)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=AleksSem&repository=xiaozhi-assistant&category=integration)

HACS-compatible Home Assistant integration that makes [Xiaozhi AI](https://xiaozhi.me) a **standard voice and text assistant**. Talk to Xiaozhi through HA Assist chat, [Voice PE speaker](https://www.home-assistant.io/voice-pe/), or any voice satellite — it processes your requests via LLM and controls smart home devices through MCP (Model Context Protocol).

> **Xiaozhi AI integration for Home Assistant.** Xiaozhi becomes a standard voice assistant — works through chat, Voice PE speaker, and any voice satellite. Processes requests via LLM and controls smart home devices.

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [MCP Tools](#mcp-tools)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)
- [Support](#support)
- [License](#license)

## Features

- **Standard Conversation Agent** — appears in HA Assist Pipeline, works with chat and voice
- **Built-in STT & TTS** — Xiaozhi's own speech recognition and voice synthesis via a single WebSocket request, no need for separate Whisper/Piper
- **Voice PE & Satellites** — full voice pipeline: speak → Xiaozhi STT → LLM → Xiaozhi TTS → hear response
- **Smart Home Control** — Xiaozhi LLM calls HA services (lights, switches, climate, etc.) via built-in MCP tools
- **Cloud & Self-hosted** — Xiaozhi Cloud with automatic OTA activation, or your own server
- **OTA Activation** — no manual tokens: get a 6-digit code, enter on xiaozhi.me, done
- **Multi-language** — supports any language that Xiaozhi LLM understands
- **Persistent WebSocket** — single connection with automatic reconnection
- **No YAML** — full UI configuration through Home Assistant

## How It Works

**Text mode** (chat widget):
```
You type text
  → XiaozhiConversation sends text over WebSocket
  → Xiaozhi LLM processes request (+MCP tool calls if needed)
  → Returns text answer
  → You read the response
```

**Voice mode** (Voice PE, satellites, companion app microphone):
```
You speak
  → XiaozhiSTT streams audio to Xiaozhi over WebSocket
  → Xiaozhi does STT + LLM + TTS in one request
  → STT text returned immediately to HA pipeline
  → LLM response + TTS audio collected in background
  → XiaozhiConversation reads cached LLM response
  → XiaozhiTTS serves cached audio (opus → WAV)
  → You hear the response
```

In voice mode, one Xiaozhi request serves all three HA pipeline stages (STT → Conversation → TTS) via caching.

## Requirements

- Home Assistant **2025.7** or newer
- `ffmpeg` on PATH for voice mode (included in HA OS by default)
- For **Cloud**: account on [xiaozhi.me](https://xiaozhi.me)
- For **Self-hosted**: running Xiaozhi server with WebSocket endpoint

## Installation

### HACS (Recommended)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=AleksSem&repository=xiaozhi-assistant&category=integration)
1. Open **HACS** in Home Assistant
2. Click **three dots** menu (top right) → **Custom repositories**
3. Add URL: `https://github.com/alekssem/xiaozhi-assistant`, category: **Integration**
4. Find **Xiaozhi AI Conversation** in HACS → **Download**
5. **Restart** Home Assistant

### Manual

1. Download `custom_components/xiaozhi` from this repository
2. Copy to `config/custom_components/xiaozhi/` in your HA installation
3. **Restart** Home Assistant

## Configuration

Go to **Settings** → **Devices & Services** → **Add Integration** → search **Xiaozhi AI Conversation**.

### Cloud Setup (xiaozhi.me)

**In Home Assistant:**

1. **Settings** → **Devices & Services** → **Add Integration** → search **Xiaozhi AI Conversation**
2. Select **Xiaozhi Cloud (xiaozhi.me)**
3. A **6-digit activation code** appears (valid for 5 minutes)

**On xiaozhi.me:**

4. Go to [xiaozhi.me](https://xiaozhi.me) → click **Console** (控制台)
5. Register / log in (phone number → SMS code → confirm)
6. **Create Agent** (添加智能体) → give it a name → configure role, voice, language, LLM model, system prompt → **Save**
7. Click **Add Device** (添加设备) → enter the 6-digit code from HA → **Confirm**

**Back in Home Assistant:**

8. The integration detects the activation automatically (polls every 3 seconds) and connects

That's it. No tokens, no URLs — everything is handled via OTA activation.

> **MCP on a separate endpoint?** If your Xiaozhi Cloud agent uses a dedicated MCP WebSocket URL, set it after setup: **Settings** → **Devices & Services** → **Xiaozhi AI Conversation** → **Configure** → enter the MCP WebSocket URL.

### Self-Hosted Setup

1. Select **Self-hosted server**
2. Enter your server's WebSocket URL (e.g., `ws://192.168.1.100:8000/xiaozhi/v1/`)
3. Enter access token (optional, depends on your server config)
4. Enter MCP WebSocket URL (optional) — if your server exposes MCP on a separate endpoint (e.g., `ws://192.168.1.100:8000/mcp/v1/`). Leave empty if MCP runs over the main WebSocket.
5. Click **Submit** — the integration validates the connection

### Post-Setup Verification

After either Cloud or Self-hosted setup:

1. **Check status**: Settings → Devices & Services → Xiaozhi AI Conversation — should show **Connected**
2. **Test text**: Open Assist → select **Xiaozhi AI** → type "Hello" → verify you get a response
3. **Test voice** (optional): Settings → Voice assistants → create/edit pipeline with Xiaozhi STT + Conversation + TTS → try speaking
4. **Logs**: If something doesn't work, enable [debug logging](#enable-debug-logging) and check `custom_components.xiaozhi`

### Options

After setup: **Settings** → **Devices & Services** → **Xiaozhi AI Conversation** → **Configure**

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| Response timeout | 30s | 5–120s | Max wait time for Xiaozhi response |
| MCP WebSocket URL | *(empty)* | — | Separate MCP endpoint URL. Leave empty if MCP uses the main WebSocket connection |

## Usage

### Text Chat

1. Open **Assist** (chat icon, bottom left in HA)
2. Select **Xiaozhi AI** as the conversation agent
3. Type your message

### Voice (Voice PE / Satellites)

1. Go to **Settings** → **Voice assistants**
2. Create or edit a pipeline
3. Set **Speech-to-text** to **Xiaozhi STT**
4. Set **Conversation agent** to **Xiaozhi AI**
5. Set **Text-to-speech** to **Xiaozhi TTS**
6. Use with Voice PE speaker, ESP32 satellite, companion app, or any supported device

> You can also mix engines: use Whisper for STT + Xiaozhi for Conversation + Piper for TTS. But using all three Xiaozhi entities together gives the best experience — one server request handles everything.

### Example Commands

| Command | What Xiaozhi Does |
|---------|-------------------|
| "Turn on the kitchen light" | Calls `light.turn_on` for the kitchen |
| "What's the temperature?" | Reads sensor state |
| "Turn off all lights" | Calls service for all light entities |
| "List all devices" | Returns entity list |
| "Set thermostat to 22" | Calls `climate.set_temperature` |
| "What was the temperature last night?" | Reads state history via recorder |
| "Run the goodnight script" | Executes `script.goodnight` |

## MCP Tools

Xiaozhi LLM can call these tools to interact with your Home Assistant:

### `homeassistant_call_service`

Call any HA service (turn_on, turn_off, toggle, set_temperature, etc.)

| Parameter | Required | Description |
|-----------|----------|-------------|
| `domain` | Yes | Service domain (light, switch, climate, etc.) |
| `service` | Yes | Service name (turn_on, turn_off, toggle, etc.) |
| `service_data` | No | Additional parameters |
| `target` | No | Target entity_id, area_id, or device_id |

### `homeassistant_get_states`

Get current state and attributes of entities.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `entity_ids` | Yes | Entity ID or list of IDs |

### `homeassistant_list_entities`

List available entities, optionally filtered.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `domain` | No | Domain filter (light, sensor, etc.) |

### `homeassistant_get_history`

Get state change history for entities over a time period. Requires the `recorder` component.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `entity_ids` | Yes | Entity ID or list of IDs |
| `hours` | No | Hours to look back (default: 24) |

### `homeassistant_get_areas`

List Home Assistant areas with optional device and entity details.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `include_devices` | No | Include devices in each area (default: false) |
| `include_entities` | No | Include entities in each area (default: false) |

### `homeassistant_fire_event`

Fire a custom event on the Home Assistant event bus.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `event_type` | Yes | The event type to fire |
| `event_data` | No | Event data payload |

### `homeassistant_execute_action`

Execute a script or trigger an automation by entity_id. Supports `script.*` and `automation.*` entities.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `entity_id` | Yes | Entity ID of the script or automation |
| `variables` | No | Variables to pass (scripts only) |

## Architecture

```
custom_components/xiaozhi/
├── __init__.py        # Entry point: setup, teardown, wiring
├── base_ws.py         # Abstract base WebSocket client: connect, reconnect, SSL
├── base_entity.py     # Base entity mixin: shared device_info for all entities
├── client.py          # WebSocket client (extends BaseWebSocketClient): hello, send_text, audio, MCP
├── stt.py             # STT entity (extends XiaozhiBaseEntity): streams audio, background collection
├── conversation.py    # Conversation entity (extends XiaozhiBaseEntity): voice cache or send_text
├── tts.py             # TTS entity (extends XiaozhiBaseEntity): cached audio or silence fallback
├── audio.py           # Audio: binary frames, PCM↔opus (FFmpeg), OGG/Opus stream build/parse
├── config_flow.py     # UI setup: Cloud (OTA) or Self-hosted (manual)
├── ota.py             # OTA activation: register device, get WS credentials
├── mcp_handler.py     # MCP JSON-RPC 2.0: initialize, tools/list, tools/call
├── mcp_client.py      # MCP WebSocket client (extends BaseWebSocketClient) for separate endpoint
├── models.py          # Data models, VoicePipelineSession, pipeline cache, PipelineResultCollector
├── const.py           # Constants: URLs, message types, timeouts, reconnect params, audio params
├── manifest.json      # HA integration metadata
├── strings.json       # UI strings (source of truth)
└── translations/
    ├── en.json        # English
    └── ru.json        # Russian
```

**Key design decisions:**
- **Dual-mode WebSocket** — text mode sends text only; voice mode streams opus audio + receives audio back
- **Non-blocking STT** — returns recognized text immediately, collects LLM response + TTS audio in a background task via `PipelineResultCollector`
- **Single persistent connection** via `BaseWebSocketClient` base class with exponential backoff reconnection (5s → 60s)
- **Pipeline caching** — one Xiaozhi request serves all three HA pipeline stages (STT → Conversation → TTS)
- **MCP over same WebSocket** — tool calls wrapped in `{"type":"mcp","payload":{JSON-RPC 2.0}}`
- **Voice mode skips chat_log** — avoids `InvalidStateError` race between HA's streaming and non-streaming response paths

## Troubleshooting

### "Unable to connect to the Xiaozhi server"
- **Cloud**: check internet connection, try re-adding the integration
- **Self-hosted**: verify server URL is correct, server is running, and reachable from HA

### Integration shows "Unavailable"
- WebSocket connection lost — auto-reconnect is active, check logs
- Server may be down or restarted

### No response / Timeout
- Increase **Response timeout** in integration options
- Check server logs for LLM processing issues

### MCP tool calls not working
- Verify MCP is enabled on your Xiaozhi server
- Check HA logs: `Logger: custom_components.xiaozhi.mcp_handler`
- Ensure target entities exist in HA

### Activation code not working
- Code expires after 5 minutes — request a new one by re-adding the integration
- Make sure you're entering the code on [xiaozhi.me](https://xiaozhi.me) device management page

### Voice mode: `InvalidStateError` crash
- This happens when Xiaozhi Conversation entity is used in a voice pipeline but STT or TTS is set to a different provider
- All three Xiaozhi entities (STT, Conversation, TTS) must be used together in the voice pipeline — Xiaozhi processes the full pipeline in one request, and the conversation entity expects cached results from STT
- If you only need text chat, using XiaozhiConversation alone is fine

### Voice mode: no audio response
- Ensure all three Xiaozhi entities (STT, Conversation, TTS) are selected in the voice pipeline
- Check that `ffmpeg` is available on the system (`ffmpeg -version` in HA terminal)
- Check logs: `custom_components.xiaozhi.stt`, `custom_components.xiaozhi.tts`

### Enable debug logging

```yaml
logger:
  logs:
    custom_components.xiaozhi: debug
```

## Support

If you'd like to support me or buy me a coffee:

[![Donate with PayPal](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/donate/?business=96G47VVQMMLFW&no_recurring=0&currency_code=EUR)

## Contributing

Issues and PRs welcome at [github.com/alekssem/xiaozhi-assistant](https://github.com/alekssem/xiaozhi-assistant).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
