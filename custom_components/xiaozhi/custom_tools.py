"""Compile and register user-defined custom MCP tools.

Users create tools via the HA admin UI (Settings → Configure → Add Custom Tool).
Each tool contains Python code that becomes the body of an async function:

    async def execute(hass, params):
        <user code here>

The function receives the HomeAssistant instance and a dict of parameters
passed by the AI. It should return a dict, list, or str with the result.
"""

from __future__ import annotations

import json
import logging
import textwrap
import uuid
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant

from .mcp_handler import MCPHandler, MCPTool

_LOGGER = logging.getLogger(__name__)

_MAX_RESULT_LEN = 8000  # truncate tool output to prevent LLM context overflow

TOOL_TEMPLATES: dict[str, dict[str, str]] = {
    "joke_en": {
        "label": "Random Joke (English)",
        "name": "tell_joke",
        "description": "Tell a random joke in English. Call this when the user asks for a joke.",
        "params_json": "{}",
        "code": textwrap.dedent("""\
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            session = async_get_clientsession(hass)
            async with session.get(
                "https://official-joke-api.appspot.com/random_joke"
            ) as resp:
                data = await resp.json()
            return {"setup": data["setup"], "punchline": data["punchline"]}"""),
    },
    "joke_ru": {
        "label": "Random Joke (Russian)",
        "name": "tell_joke_ru",
        "description": "Расскажи случайный анекдот. Вызывай когда просят анекдот или шутку.",
        "params_json": "{}",
        "code": textwrap.dedent("""\
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            import re
            session = async_get_clientsession(hass)
            async with session.get(
                "http://rzhunemogu.ru/RandJSON.aspx?CType=1"
            ) as resp:
                raw = await resp.read()
            text = raw.decode("windows-1251", errors="replace")
            match = re.search(r'"content"\\s*:\\s*"(.+)"', text, flags=re.DOTALL)
            joke = match.group(1).strip() if match else text.strip()
            return {"joke": joke}"""),
    },
    "weather": {
        "label": "Current Weather (Open-Meteo)",
        "name": "get_weather",
        "description": (
            "Get current weather for a location. Uses Home Assistant's"
            " configured location by default. Free, no API key needed."
        ),
        "params_json": (
            '{"latitude": {"type": "number", "description": "Latitude"},'
            ' "longitude": {"type": "number", "description": "Longitude"}}'
        ),
        "code": textwrap.dedent("""\
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            lat = params.get("latitude") or hass.config.latitude
            lon = params.get("longitude") or hass.config.longitude
            session = async_get_clientsession(hass)
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}&current_weather=true"
            )
            async with session.get(url) as resp:
                data = await resp.json()
            return data["current_weather"]"""),
    },
    "fetch_webpage": {
        "label": "Fetch Webpage",
        "name": "fetch_webpage",
        "description": (
            "Fetch and extract text content from any URL."
            " Strips HTML tags, scripts, and styles."
        ),
        "params_json": '{"url": {"type": "string", "description": "URL to fetch"}}',
        "code": textwrap.dedent("""\
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            import re
            url = params["url"]
            session = async_get_clientsession(hass)
            async with session.get(url) as resp:
                html = await resp.text()
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = " ".join(text.split())
            return {"content": text[:5000], "url": url}"""),
    },
    "rss_news": {
        "label": "RSS News Reader",
        "name": "read_news",
        "description": (
            "Read latest news from an RSS feed."
            " Default: rus.delfi.lv. Returns titles, links, and short descriptions."
        ),
        "params_json": (
            '{"url": {"type": "string", "description": "RSS feed URL"},'
            ' "count": {"type": "number", "description": "Number of articles (default 5)"}}'
        ),
        "code": textwrap.dedent("""\
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            import xml.etree.ElementTree as ET
            import re
            url = params.get("url", "https://rus.delfi.lv/rss/index.xml")
            count = int(params.get("count", 5))
            session = async_get_clientsession(hass)
            async with session.get(url) as resp:
                raw = await resp.read()
            items = []
            try:
                root = ET.fromstring(raw)
                for item in root.findall(".//item")[:count]:
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    desc = item.findtext("description", "")
                    items.append({"title": title, "link": link, "description": desc[:200]})
            except ET.ParseError:
                text = raw.decode("utf-8", errors="replace")
                for m in re.finditer(r"<item[^>]*>(.*?)</item>", text, re.DOTALL):
                    if len(items) >= count:
                        break
                    block = m.group(1)
                    t = re.search(r"<title[^>]*>(.*?)</title>", block, re.DOTALL)
                    l = re.search(r"<link[^>]*>(.*?)</link>", block, re.DOTALL)
                    d = re.search(r"<description[^>]*>(.*?)</description>", block, re.DOTALL)
                    title = t.group(1).strip() if t else ""
                    link = l.group(1).strip() if l else ""
                    desc = re.sub(r"<[^>]+>", "", d.group(1).strip() if d else "")[:200]
                    if title or link:
                        items.append({"title": title, "link": link, "description": desc})
            return {"news": items, "source": url}"""),
    },
}


def register_custom_tools(
    hass: HomeAssistant,
    mcp_handler: MCPHandler,
    tools_config: list[dict[str, Any]],
) -> int:
    """Register custom tools from config. Returns count registered."""
    count = 0
    for tool_cfg in tools_config:
        try:
            tool = _compile_tool(tool_cfg)
            mcp_handler.register_tool(tool)
            _LOGGER.info("Registered custom tool: %s", tool.name)
            count += 1
        except Exception:
            _LOGGER.warning(
                "Failed to load custom tool '%s'",
                tool_cfg.get("name", "?"),
                exc_info=True,
            )
    return count


def _compile_tool(cfg: dict[str, Any]) -> MCPTool:
    """Compile a single tool config into MCPTool."""
    name = cfg["name"]
    description = cfg["description"]
    code = cfg["code"]
    params_json = cfg.get("params_json", "{}")

    # Parse parameter definitions
    try:
        params = json.loads(params_json) if params_json.strip() else {}
    except json.JSONDecodeError:
        params = {}

    # Compile user code into an async function
    indented = textwrap.indent(code, "    ")
    wrapped = f"async def _execute(hass, params):\n{indented}\n"
    namespace: dict[str, Any] = {}
    exec(compile(wrapped, f"<custom_tool:{name}>", "exec"), namespace)  # noqa: S102
    execute_fn = namespace["_execute"]

    return MCPTool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": params},
        handler=partial(_tool_wrapper, execute_fn),
    )


async def _tool_wrapper(
    execute_fn: Any,
    hass: HomeAssistant,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Execute user function with error handling and truncation."""
    try:
        result = await execute_fn(hass, params)
    except Exception as err:
        _LOGGER.warning("Custom tool execution error: %s", err, exc_info=True)
        return {"error": f"{type(err).__name__}: {err}"}

    # Normalize result to dict
    if isinstance(result, str):
        result = {"content": result}
    elif isinstance(result, list):
        result = {"items": result}
    elif not isinstance(result, dict):
        result = {"content": str(result)}

    # Truncate to prevent LLM context overflow
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) > _MAX_RESULT_LEN:
        result = {"content": text[:_MAX_RESULT_LEN], "truncated": True}

    return result


def generate_tool_id() -> str:
    """Generate a short unique tool ID."""
    return uuid.uuid4().hex[:8]
