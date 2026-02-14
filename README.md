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
- [Custom Tools](#custom-tools)
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
| Custom Tools | — | — | Add, edit, test, or delete custom Python tools. Includes ready-made templates |

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

## Custom Tools

You can extend Xiaozhi AI with **any capability** by creating custom Python tools directly in the HA admin UI. Each tool becomes an MCP function that the AI can call — no files, no YAML, no SSH.

### Templates

The quickest way to add a tool — pick a ready-made template and customize it:

1. **Configure** → **Custom Tools** → select **"Add from template"**
2. Choose a template → form pre-fills with working code
3. Customize name/description/code if needed → **Submit**

**Available templates:**

| Template | What it does |
|----------|-------------|
| Random Joke (English) | Fetches a random joke from official-joke-api |
| Random Joke (Russian) | Fetches a random joke from rzhunemogu.ru |
| Current Weather (Open-Meteo) | Current weather for any location, no API key |
| Fetch Webpage | Fetches and cleans HTML from any URL |
| RSS News Reader | Reads news from any RSS feed (default: delfi.lv) |

### How It Works

1. Go to **Settings** → **Devices & Services** → **Xiaozhi AI Conversation** → **Configure**
2. Click **Custom Tools** → select **"Add custom tool"** or **"Add from template"**
3. Fill in the form:
   - **Name** — MCP tool name (e.g., `fetch_webpage`)
   - **Description** — what the AI reads to decide when to use the tool
   - **Parameters** (optional) — JSON schema for arguments the AI can pass
   - **Python code** — the body of `async def execute(hass, params):`
4. Optionally check **"Test without saving"** to run the code first and see the result
5. Click **Submit** — the integration compiles the code and registers the tool
6. The AI automatically sees the new tool and can call it

### Example 1: Random Joke (zero config)

Works immediately — just paste and submit. No API keys needed.

**Name:** `tell_joke`

**Description:** `Tell a random joke. Use this when the user asks for a joke or wants to have fun.`

**Parameters:** *(leave empty)*

**Python code:**
```python
from homeassistant.helpers.aiohttp_client import async_get_clientsession

session = async_get_clientsession(hass)
async with session.get("https://official-joke-api.appspot.com/random_joke") as resp:
    joke = await resp.json()
return {"setup": joke["setup"], "punchline": joke["punchline"]}
```

**Say to Xiaozhi:** "Tell me a joke"

---

### Example 2: Current Weather (zero config)

Get real weather for any location. Uses Open-Meteo — free, no API key.

**Name:** `get_weather`

**Description:** `Get current weather (temperature, humidity, wind) for a location by coordinates. Use this when the user asks about weather.`

**Parameters:**
```json
{
  "latitude": {
    "type": "number",
    "description": "Latitude (e.g. 55.75 for Moscow, 48.85 for Paris)"
  },
  "longitude": {
    "type": "number",
    "description": "Longitude (e.g. 37.62 for Moscow, 2.35 for Paris)"
  }
}
```

**Python code:**
```python
from homeassistant.helpers.aiohttp_client import async_get_clientsession

lat = params.get("latitude", 55.75)
lon = params.get("longitude", 37.62)

session = async_get_clientsession(hass)
url = (
    f"https://api.open-meteo.com/v1/forecast?"
    f"latitude={lat}&longitude={lon}"
    f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
    f"&timezone=auto"
)
async with session.get(url) as resp:
    data = await resp.json()

current = data["current"]
return {
    "temperature": f"{current['temperature_2m']}°C",
    "humidity": f"{current['relative_humidity_2m']}%",
    "wind": f"{current['wind_speed_10m']} km/h",
}
```

**Say to Xiaozhi:** "What's the weather in Paris?" or "How's the weather?"

---

### Example 3: Fetch a Web Page

Read content from any URL. The AI can browse the internet.

**Name:** `fetch_webpage`

**Description:** `Fetch and read text content from a URL. Use this when asked to check a website, read news, or get information from the internet.`

**Parameters:**
```json
{
  "url": {
    "type": "string",
    "description": "The URL to fetch"
  }
}
```

**Python code:**
```python
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import aiohttp
import re

session = async_get_clientsession(hass)
url = params.get("url", "")
if not url:
    return {"error": "URL is required"}

try:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        text = await resp.text()
except Exception as e:
    return {"error": str(e)}

# Strip HTML tags for readability
clean = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text)
clean = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', clean)
clean = re.sub(r'<[^>]+>', ' ', clean)
clean = re.sub(r'\s+', ' ', clean).strip()
return {"content": clean[:4000]}
```

**Say to Xiaozhi:** "Go to bbc.com and tell me the latest news"

### Example 4: Check Email (IMAP)

Read latest email subjects and senders.

**Name:** `check_email`

**Description:** `Check the email inbox and return the latest messages with subjects and senders.`

**Parameters:**
```json
{
  "count": {
    "type": "integer",
    "description": "Number of emails to check (default: 5)"
  }
}
```

**Python code:**
```python
import imaplib
import email as email_lib

# ⚠️ Replace with your credentials
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
USERNAME = "your_email@gmail.com"
PASSWORD = "your_app_password"  # Use App Password for Gmail

count = params.get("count", 5)

def _fetch():
    conn = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    conn.login(USERNAME, PASSWORD)
    conn.select("INBOX")
    _, data = conn.search(None, "ALL")
    ids = data[0].split()[-count:]
    results = []
    for msg_id in reversed(ids):
        _, msg_data = conn.fetch(msg_id, "(RFC822)")
        msg = email_lib.message_from_bytes(msg_data[0][1])
        results.append({
            "from": str(msg["From"]),
            "subject": str(msg["Subject"]),
            "date": str(msg["Date"]),
        })
    conn.logout()
    return results

emails = await hass.async_add_executor_job(_fetch)
return {"emails": emails}
```

> For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833), not your regular password.

**Say to Xiaozhi:** "Check my email" or "Do I have any new messages?"

### Example 5: Check Telegram Messages

Read recent messages from a Telegram chat via Bot API.

**Name:** `check_telegram`

**Description:** `Check recent messages in Telegram and return the latest ones.`

**Parameters:**
```json
{
  "count": {
    "type": "integer",
    "description": "Number of messages to return (default: 5)"
  }
}
```

**Python code:**
```python
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import aiohttp

# ⚠️ Replace with your bot token
BOT_TOKEN = "123456:ABC-DEF..."

count = params.get("count", 5)
session = async_get_clientsession(hass)
url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?limit={count}"

async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
    data = await resp.json()

messages = []
for update in data.get("result", []):
    msg = update.get("message", {})
    if msg:
        messages.append({
            "from": msg.get("from", {}).get("first_name", "Unknown"),
            "text": msg.get("text", ""),
            "date": msg.get("date", ""),
        })

return {"messages": messages}
```

**Say to Xiaozhi:** "Check if anyone wrote to me in Telegram"

### Tips

- **`hass`** — the HomeAssistant instance. Use `hass.services.async_call()`, `hass.states.get()`, etc.
- **`params`** — dict of arguments passed by the AI, matching your Parameters JSON schema
- **Blocking code** (imaplib, requests, file I/O) — wrap in `await hass.async_add_executor_job(func)`
- **HTTP requests** — use `async_get_clientsession(hass)` (HA's shared aiohttp session)
- **Return value** — return a `dict`, `list`, or `str`. Results are auto-truncated to 8000 chars
- **Errors** — exceptions are caught and returned to the AI as `{"error": "..."}` (no crash)
- **Editing tools** — Configure → Custom Tools → select tool name → modify → Submit
- **Removing tools** — Configure → Custom Tools → select tool → check **Delete** → Submit
- **After adding/removing** tools, the integration reloads automatically

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
├── custom_tools.py    # Custom tools: TOOL_TEMPLATES, compile user code, register as MCP tools
├── config_flow.py     # UI setup: Cloud (OTA), Self-hosted, options (settings, custom tools, templates)
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
- **Voice mode adds to chat_log without delta_listener** — avoids `InvalidStateError` race between HA's streaming and non-streaming response paths

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
