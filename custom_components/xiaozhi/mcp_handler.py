"""MCP (Model Context Protocol) handler for Xiaozhi server tool calls."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_NAME = "homeassistant-xiaozhi"
MCP_SERVER_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# Security blocklists
# ---------------------------------------------------------------------------

_BLOCKED_DOMAINS = frozenset({"shell_command", "python_script"})

_BLOCKED_SERVICES = frozenset({
    ("homeassistant", "restart"),
    ("homeassistant", "stop"),
    ("homeassistant", "reload_all"),
    ("homeassistant", "reload_config_entries"),
    ("hassio", "host_reboot"),
    ("hassio", "host_shutdown"),
    ("hassio", "addon_stop"),
})

_BLOCKED_EVENT_TYPES = frozenset({
    "homeassistant_stop",
    "homeassistant_restart",
    "call_service",
    "component_loaded",
    "service_registered",
})


# ---------------------------------------------------------------------------
# MCPTool dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCPTool:
    """Definition of a single MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[HomeAssistant, dict[str, Any]], Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Standalone handler functions
# ---------------------------------------------------------------------------


async def _tool_call_service(
    hass: HomeAssistant, params: dict[str, Any]
) -> dict[str, Any]:
    """Call a Home Assistant service."""
    domain = params.get("domain")
    service = params.get("service")
    service_data = params.get("service_data", {})
    target = params.get("target", {})

    if not isinstance(domain, str) or not isinstance(service, str):
        raise ValueError("domain and service must be strings")
    if not domain or not service:
        raise ValueError("domain and service are required")
    if not isinstance(service_data, dict):
        raise ValueError("service_data must be a dict")
    if not isinstance(target, dict):
        raise ValueError("target must be a dict")

    if domain in _BLOCKED_DOMAINS:
        raise ValueError(f"Service domain '{domain}' is blocked for security reasons")
    if (domain, service) in _BLOCKED_SERVICES:
        raise ValueError(f"Service '{domain}.{service}' is blocked for security reasons")

    await hass.services.async_call(
        domain,
        service,
        service_data,
        target=target,
        blocking=True,
    )

    return {"success": True}


_EXCLUDED_ATTRS = frozenset({
    "supported_features",
    "supported_color_modes",
    "color_mode",
    "entity_picture",
    "entity_picture_local",
    "icon",
    "attribution",
    "assumed_state",
    "effect_list",
    "preset_modes",
    "hvac_modes",
    "fan_modes",
    "swing_modes",
    "source_list",
    "sound_mode_list",
    "options",
})


async def _tool_get_states(
    hass: HomeAssistant, params: dict[str, Any]
) -> dict[str, Any]:
    """Get states of specified entities."""
    entity_ids = params.get("entity_ids", [])

    if not isinstance(entity_ids, (str, list)):
        raise ValueError("entity_ids must be a string or list of strings")
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]

    states = {}
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state:
            attrs = {
                k: v
                for k, v in state.attributes.items()
                if k not in _EXCLUDED_ATTRS
                and not isinstance(v, (list, set))
            }
            states[entity_id] = {
                "state": state.state,
                "attributes": attrs,
                "last_changed": state.last_changed.isoformat(),
            }
        else:
            states[entity_id] = None

    return {"states": states}


async def _tool_list_entities(
    hass: HomeAssistant, params: dict[str, Any]
) -> dict[str, Any]:
    """List available entities, optionally filtered by domain."""
    domain_filter = params.get("domain")
    limit = min(int(params.get("limit", 200)), 500)

    entities = []
    for state in hass.states.async_all():
        if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
            continue
        entities.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", ""),
            }
        )

    # Sort: available first, then alphabetically by entity_id
    entities.sort(key=lambda e: (e["state"] == "unavailable", e["entity_id"]))

    total_count = len(entities)
    truncated = total_count > limit
    entities = entities[:limit]

    result: dict[str, Any] = {"entities": entities, "total": total_count}
    if truncated:
        result["truncated"] = True
        result["hint"] = "Use 'domain' filter to narrow results"

    return result


async def _tool_get_history(
    hass: HomeAssistant, params: dict[str, Any]
) -> dict[str, Any]:
    """Get state history for entities over a time period."""
    try:
        from homeassistant.components.recorder import history  # noqa: F811
    except ImportError as err:
        raise ValueError(
            "Recorder component is not available. "
            "Make sure 'recorder' is configured in Home Assistant."
        ) from err

    entity_ids = params.get("entity_ids", [])
    if not isinstance(entity_ids, (str, list)):
        raise ValueError("entity_ids must be a string or list of strings")
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]

    if not entity_ids:
        raise ValueError("entity_ids is required")

    hours = max(1, min(int(params.get("hours", 24)), 8760))
    now = datetime.now()
    start = now - timedelta(hours=hours)

    states_by_entity = await hass.async_add_executor_job(
        partial(
            history.state_changes_during_period,
            hass,
            start,
            now,
            entity_ids=entity_ids,
        )
    )

    result: dict[str, list[dict[str, Any]]] = {}
    for eid, states in states_by_entity.items():
        result[eid] = [
            {
                "state": s.state,
                "last_changed": s.last_changed.isoformat(),
            }
            for s in states
        ]

    return {"history": result}


async def _tool_get_areas(
    hass: HomeAssistant, params: dict[str, Any]
) -> dict[str, Any]:
    """List areas with optional device/entity details."""
    from homeassistant.helpers import area_registry as ar

    include_devices = bool(params.get("include_devices", False))
    include_entities = bool(params.get("include_entities", False))

    area_reg = ar.async_get(hass)

    # Lazy-load registries only when needed
    dev_reg = None
    ent_reg = None
    if include_devices:
        from homeassistant.helpers import device_registry as dr

        dev_reg = dr.async_get(hass)
    if include_entities:
        from homeassistant.helpers import entity_registry as er

        ent_reg = er.async_get(hass)

    areas: list[dict[str, Any]] = []
    for area in area_reg.async_list_areas():
        entry: dict[str, Any] = {"id": area.id, "name": area.name}

        if dev_reg is not None:
            entry["devices"] = [
                {"id": d.id, "name": d.name_by_user or d.name}
                for d in dev_reg.devices.get_devices_for_area_id(area.id)
            ]

        if ent_reg is not None:
            entry["entities"] = [
                {"entity_id": e.entity_id, "name": e.name or e.original_name}
                for e in ent_reg.entities.get_entries_for_area_id(area.id)
            ]

        areas.append(entry)

    return {"areas": areas}


async def _tool_fire_event(
    hass: HomeAssistant, params: dict[str, Any]
) -> dict[str, Any]:
    """Fire a Home Assistant event."""
    event_type = params.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event_type must be a non-empty string")

    if event_type in _BLOCKED_EVENT_TYPES:
        raise ValueError(f"Event type '{event_type}' is blocked for security reasons")

    event_data = params.get("event_data", {})
    if not isinstance(event_data, dict):
        raise ValueError("event_data must be a dict")

    hass.bus.async_fire(event_type, event_data)

    return {"success": True}


async def _tool_execute_action(
    hass: HomeAssistant, params: dict[str, Any]
) -> dict[str, Any]:
    """Execute a script or trigger an automation by entity_id."""
    entity_id = params.get("entity_id", "")
    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("entity_id must be a non-empty string")

    variables = params.get("variables", {})
    if not isinstance(variables, dict):
        raise ValueError("variables must be a dict")

    if entity_id.startswith("script."):
        service_data: dict[str, Any] = {"entity_id": entity_id}
        if variables:
            service_data["variables"] = variables
        await hass.services.async_call(
            "script", "turn_on", service_data, blocking=True
        )
    elif entity_id.startswith("automation."):
        await hass.services.async_call(
            "automation",
            "trigger",
            {"entity_id": entity_id},
            blocking=True,
        )
    else:
        raise ValueError(
            f"Unsupported entity type: {entity_id}. "
            "Only script.* and automation.* are supported."
        )

    return {"success": True}


_SUMMARY_SENSOR_CLASSES = frozenset({
    "temperature", "humidity", "illuminance", "pm25", "pm10",
    "co2", "battery", "power", "energy", "pressure",
})

_SUMMARY_DEVICE_DOMAINS = frozenset({
    "light", "switch", "fan", "cover", "climate",
    "media_player", "lock", "vacuum",
})

_ON_STATES = frozenset({
    "on", "open", "unlocked", "playing",
    "heat", "cool", "auto", "cleaning",
})


async def _tool_get_home_summary(
    hass: HomeAssistant, params: dict[str, Any]
) -> dict[str, Any]:
    """Get a compact home summary: areas, sensors, device counts, weather."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    area_filter = params.get("area_id")

    area_reg = ar.async_get(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Build entity_id -> area_id mapping (entity area overrides device area)
    entity_area: dict[str, str] = {}
    for entry in ent_reg.entities.values():
        if entry.area_id:
            entity_area[entry.entity_id] = entry.area_id
        elif entry.device_id:
            device = dev_reg.async_get(entry.device_id)
            if device and device.area_id:
                entity_area[entry.entity_id] = device.area_id

    # Prepare area buckets
    areas_data: dict[str, dict[str, Any]] = {}
    for area in area_reg.async_list_areas():
        if area_filter and area.id != area_filter:
            continue
        areas_data[area.id] = {
            "name": area.name,
            "sensors": [],
            "devices": {},
        }

    # Single pass over all states
    all_states = hass.states.async_all()
    for state in all_states:
        area_id = entity_area.get(state.entity_id)
        if not area_id or area_id not in areas_data:
            continue

        ad = areas_data[area_id]
        domain = state.entity_id.split(".")[0]
        attrs = state.attributes

        # Collect key sensor readings
        if domain in ("sensor", "binary_sensor"):
            dc = attrs.get("device_class", "")
            if dc in _SUMMARY_SENSOR_CLASSES and state.state not in (
                "unknown",
                "unavailable",
            ):
                try:
                    val = round(float(state.state), 1)
                    unit = attrs.get("unit_of_measurement", "")
                    name = attrs.get("friendly_name", state.entity_id)
                    ad["sensors"].append(f"{name}: {val} {unit}".strip())
                except (ValueError, TypeError):
                    pass

        # Count device statuses
        if domain in _SUMMARY_DEVICE_DOMAINS:
            if domain not in ad["devices"]:
                ad["devices"][domain] = {"on": 0, "off": 0, "unavailable": 0}
            counts = ad["devices"][domain]
            if state.state == "unavailable":
                counts["unavailable"] += 1
            elif state.state in _ON_STATES:
                counts["on"] += 1
            else:
                counts["off"] += 1

    # Build compact result — skip empty areas
    result_areas = []
    for ad in areas_data.values():
        if not ad["sensors"] and not ad["devices"]:
            continue
        area_entry: dict[str, Any] = {"name": ad["name"]}
        if ad["sensors"]:
            area_entry["sensors"] = ad["sensors"]
        if ad["devices"]:
            area_entry["devices"] = ad["devices"]
        result_areas.append(area_entry)

    result: dict[str, Any] = {"areas": result_areas}

    # Weather (first available)
    for state in all_states:
        if state.entity_id.startswith("weather.") and state.state != "unavailable":
            result["weather"] = {
                "condition": state.state,
                "temperature": state.attributes.get("temperature"),
                "humidity": state.attributes.get("humidity"),
                "unit": state.attributes.get("temperature_unit", "°C"),
            }
            break

    # Overall stats
    total = len(all_states)
    unavailable = sum(1 for s in all_states if s.state == "unavailable")
    result["total_entities"] = total
    result["unavailable_count"] = unavailable

    return result


# ---------------------------------------------------------------------------
# Tool definitions (constants)
# ---------------------------------------------------------------------------

TOOL_CALL_SERVICE = MCPTool(
    name="homeassistant_call_service",
    description="Call a Home Assistant service (e.g., turn on light, toggle switch)",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Service domain (e.g., light, switch, climate)",
            },
            "service": {
                "type": "string",
                "description": "Service name (e.g., turn_on, turn_off, toggle)",
            },
            "service_data": {
                "type": "object",
                "description": "Additional service data",
            },
            "target": {
                "type": "object",
                "description": "Target entities, areas, or devices",
                "properties": {
                    "entity_id": {},
                    "area_id": {},
                    "device_id": {},
                },
            },
        },
        "required": ["domain", "service"],
    },
    handler=_tool_call_service,
)

TOOL_GET_STATES = MCPTool(
    name="homeassistant_get_states",
    description="Get the current state and attributes of Home Assistant entities",
    input_schema={
        "type": "object",
        "properties": {
            "entity_ids": {
                "description": "Entity ID or list of entity IDs to query",
            },
        },
        "required": ["entity_ids"],
    },
    handler=_tool_get_states,
)

TOOL_LIST_ENTITIES = MCPTool(
    name="homeassistant_list_entities",
    description=(
        "List available Home Assistant entities, optionally filtered by domain. "
        "Returns up to 200 entities by default (available entities first)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Optional domain filter (e.g., light, switch)",
            },
            "limit": {
                "type": "integer",
                "description": "Max entities to return (default: 200, max: 500)",
            },
        },
    },
    handler=_tool_list_entities,
)

TOOL_GET_HISTORY = MCPTool(
    name="homeassistant_get_history",
    description=(
        "Get state change history for entities over a time period. "
        "Requires the recorder component."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_ids": {
                "description": "Entity ID or list of entity IDs to query",
            },
            "hours": {
                "type": "integer",
                "description": "Number of hours to look back (default: 24)",
            },
        },
        "required": ["entity_ids"],
    },
    handler=_tool_get_history,
)

TOOL_GET_AREAS = MCPTool(
    name="homeassistant_get_areas",
    description="List Home Assistant areas with optional device and entity details",
    input_schema={
        "type": "object",
        "properties": {
            "include_devices": {
                "type": "boolean",
                "description": "Include devices in each area (default: false)",
            },
            "include_entities": {
                "type": "boolean",
                "description": "Include entities in each area (default: false)",
            },
        },
    },
    handler=_tool_get_areas,
)

TOOL_GET_HOME_SUMMARY = MCPTool(
    name="homeassistant_get_home_summary",
    description=(
        "Get a concise summary of the entire home: areas with sensor readings "
        "(temperature, humidity, etc.), device status counts per area, current "
        "weather, and overall availability stats. Use this FIRST when the user "
        "asks about the home status."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "area_id": {
                "type": "string",
                "description": "Optional area ID to filter (omit for full home summary)",
            },
        },
    },
    handler=_tool_get_home_summary,
)

TOOL_FIRE_EVENT = MCPTool(
    name="homeassistant_fire_event",
    description="Fire a custom event on the Home Assistant event bus",
    input_schema={
        "type": "object",
        "properties": {
            "event_type": {
                "type": "string",
                "description": "The event type to fire",
            },
            "event_data": {
                "type": "object",
                "description": "Optional event data payload",
            },
        },
        "required": ["event_type"],
    },
    handler=_tool_fire_event,
)

TOOL_EXECUTE_ACTION = MCPTool(
    name="homeassistant_execute_action",
    description=(
        "Execute a script or trigger an automation by entity_id. "
        "Supports script.* and automation.* entities."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Entity ID of the script or automation to execute",
            },
            "variables": {
                "type": "object",
                "description": "Optional variables to pass (scripts only)",
            },
        },
        "required": ["entity_id"],
    },
    handler=_tool_execute_action,
)

DEFAULT_TOOLS: list[MCPTool] = [
    TOOL_CALL_SERVICE,
    TOOL_GET_STATES,
    TOOL_LIST_ENTITIES,
    TOOL_GET_HOME_SUMMARY,
    TOOL_GET_HISTORY,
    TOOL_GET_AREAS,
    TOOL_FIRE_EVENT,
    TOOL_EXECUTE_ACTION,
]


# ---------------------------------------------------------------------------
# MCPHandler
# ---------------------------------------------------------------------------


class MCPHandler:
    """Handles MCP JSON-RPC requests from Xiaozhi server."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the MCP handler."""
        self._hass = hass
        self._initialized = False
        self._tools: dict[str, MCPTool] = {}

        for tool in DEFAULT_TOOLS:
            self.register_tool(tool)

    # -- public API for dynamic tool management --

    def register_tool(self, tool: MCPTool) -> None:
        """Register a tool (overwrites if name already exists)."""
        self._tools[tool.name] = tool

    def unregister_tool(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    # -- request handling --

    async def handle_request(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a JSON-RPC 2.0 request and return a response."""
        method = data.get("method")
        params = data.get("params", {})
        request_id = data.get("id")

        if method is None:
            if request_id is not None:
                _LOGGER.warning("MCP request %s missing method", request_id)
                return self._error_response(request_id, -32600, "Missing method")
            return None

        # JSON-RPC notifications (no id) — no response needed
        if request_id is None:
            _LOGGER.debug("MCP notification: %s", method)
            return None

        try:
            result = await self._dispatch(method, params)
            return self._success_response(request_id, result)
        except Exception as err:
            _LOGGER.warning("MCP method failed: %s - %s", method, err)
            return self._error_response(request_id, -32000, str(err))

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Dispatch a method call to the appropriate handler."""
        if method == "initialize":
            return self._handle_initialize(params)

        if method == "tools/list":
            return self._handle_tools_list()

        if method == "tools/call":
            return await self._handle_tools_call(params)

        if method == "ping":
            return {}

        raise ValueError(f"Unknown method: {method}")

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle MCP initialize request."""
        self._initialized = True
        _LOGGER.debug(
            "MCP initialized by client: %s",
            params.get("clientInfo", {}).get("name", "unknown"),
        )
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": MCP_SERVER_NAME,
                "version": MCP_SERVER_VERSION,
            },
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        """Handle tools/list request — returns all registered tools."""
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
                for tool in self._tools.values()
            ]
        }

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call request — dispatch by tool name."""
        name = params.get("name")
        arguments = params.get("arguments", {})

        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        result = await tool.handler(self._hass, arguments)

        return {
            "content": [
                {"type": "text", "text": json.dumps(result, default=str)},
            ],
        }

    @staticmethod
    def _success_response(request_id: Any, result: Any) -> dict[str, Any]:
        """Build a JSON-RPC 2.0 success response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    @staticmethod
    def _error_response(
        request_id: Any, code: int, message: str
    ) -> dict[str, Any]:
        """Build a JSON-RPC 2.0 error response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
