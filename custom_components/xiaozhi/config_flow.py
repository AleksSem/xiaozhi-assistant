"""Config flow for Xiaozhi AI Conversation integration."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import textwrap
import traceback
import uuid
from typing import Any
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
)

from .client import XiaozhiWebSocketClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_MCP_URL,
    CONF_PROTOCOL_VERSION,
    CONF_RESPONSE_TIMEOUT,
    CONF_SERVER_URL,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_RESPONSE_TIMEOUT,
    DOMAIN,
    MAX_RESPONSE_TIMEOUT,
    MIN_RESPONSE_TIMEOUT,
)
from .custom_tools import TOOL_TEMPLATES, generate_tool_id
from .models import XiaozhiConfig
from .ota import OTAError, XiaozhiOTAClient

_LOGGER = logging.getLogger(__name__)


def _generate_device_id() -> str:
    """Generate a fake MAC-style device ID from UUID."""
    raw = uuid.uuid4().hex[:12]
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


class XiaozhiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xiaozhi AI Conversation."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._device_id: str | None = None
        self._client_id: str | None = None
        self._activation_code: str | None = None
        self._activation_message: str | None = None
        self._ws_url: str | None = None
        self._ws_token: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start config flow — go directly to OTA activation."""
        return await self.async_step_activate()

    async def async_step_activate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a: Request OTA activation code."""
        self._device_id = _generate_device_id()
        self._client_id = str(uuid.uuid4())

        session = async_get_clientsession(self.hass)
        ota_client = XiaozhiOTAClient(session)

        try:
            result = await ota_client.request_activation(
                self._device_id, self._client_id
            )
        except OTAError as err:
            return self.async_abort(reason="ota_failed", description_placeholders={"error": str(err)})

        if result.is_activated:
            assert result.config is not None  # guaranteed by is_activated
            _LOGGER.debug("OTA returned credentials directly (no activation code needed)")
            return await self._finish_cloud_setup(
                result.config.websocket_url,
                result.config.access_token,
            )

        self._activation_code = result.code
        self._activation_message = result.message
        if result.config:
            self._ws_url = result.config.websocket_url
            self._ws_token = result.config.access_token

        return self.async_show_form(
            step_id="poll",
            description_placeholders={
                "code": self._activation_code or "",
            },
        )

    async def async_step_poll(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b: Poll until activation completes."""
        errors: dict[str, str] = {}

        session = async_get_clientsession(self.hass)
        ota_client = XiaozhiOTAClient(session)

        try:
            assert self._device_id is not None
            assert self._client_id is not None
            ota_config = await ota_client.poll_activation(
                self._device_id,
                self._client_id,
            )
        except OTAError:
            errors["base"] = "activation_timeout"
            return self.async_show_form(
                step_id="poll",
                description_placeholders={
                    "code": self._activation_code or "",
                },
                errors=errors,
            )

        ws_url = ota_config.websocket_url
        ws_token = ota_config.access_token

        # Fall back to credentials from initial OTA call if poll didn't return new ones
        if not ws_url and self._ws_url:
            ws_url = self._ws_url
            ws_token = self._ws_token or ""

        return await self._finish_cloud_setup(ws_url, ws_token)

    async def _validate_connection(self, config: XiaozhiConfig) -> str | None:
        """Validate connection. Returns error key or None."""
        client = XiaozhiWebSocketClient(config)
        try:
            valid = await client.validate_connection()
            if not valid:
                return "cannot_connect"
        except (OSError, asyncio.TimeoutError, ConnectionError):
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error validating connection")
            return "cannot_connect"
        return None

    async def _finish_cloud_setup(
        self, websocket_url: str, access_token: str
    ) -> ConfigFlowResult:
        """Validate cloud connection and create entry."""
        assert self._device_id is not None
        assert self._client_id is not None

        config = XiaozhiConfig(
            server_url=websocket_url,
            access_token=access_token,
            device_id=self._device_id,
            client_id=self._client_id,
        )

        error = await self._validate_connection(config)
        if error:
            return self.async_abort(reason=error)

        return self.async_create_entry(
            title="Xiaozhi AI",
            data={
                CONF_SERVER_URL: websocket_url,
                CONF_ACCESS_TOKEN: access_token,
                CONF_DEVICE_ID: self._device_id,
                CONF_CLIENT_ID: self._client_id,
                CONF_PROTOCOL_VERSION: DEFAULT_PROTOCOL_VERSION,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> XiaozhiOptionsFlow:
        """Get the options flow for this handler."""
        return XiaozhiOptionsFlow()


class XiaozhiOptionsFlow(OptionsFlow):
    """Handle options for Xiaozhi AI Conversation."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._custom_tools: list[dict[str, Any]] = []
        self._editing_tool_id: str | None = None
        self._template_data: dict[str, str] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show main menu."""
        self._custom_tools = copy.deepcopy(
            self.config_entry.options.get("custom_tools", [])
        )
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "custom_tools"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """General settings (timeout)."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **user_input,
                    "custom_tools": self._custom_tools,
                }
            )

        current_timeout = self.config_entry.options.get(
            CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT
        )
        current_mcp_url = self.config_entry.options.get(CONF_MCP_URL, "")

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RESPONSE_TIMEOUT,
                        default=current_timeout,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_RESPONSE_TIMEOUT,
                            max=MAX_RESPONSE_TIMEOUT,
                        ),
                    ),
                    vol.Optional(
                        CONF_MCP_URL,
                        default=current_mcp_url,
                    ): str,
                }
            ),
        )

    async def async_step_custom_tools(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Custom tools list — select to edit, add new, or add from template."""
        if user_input is not None:
            selected = user_input["selected"]
            if selected == "__add__":
                return await self.async_step_add_tool()
            if selected == "__template__":
                return await self.async_step_add_from_template()
            self._editing_tool_id = selected
            return await self.async_step_edit_tool()

        tool_options: dict[str, str] = {
            "__add__": "➕ Add custom tool",
            "__template__": "📋 Add from template",
        }
        for tool in self._custom_tools:
            tool_options[tool["id"]] = tool["name"]

        return self.async_show_form(
            step_id="custom_tools",
            data_schema=vol.Schema(
                {
                    vol.Required("selected"): vol.In(tool_options),
                }
            ),
            description_placeholders={
                "tool_count": str(len(self._custom_tools)),
            },
        )

    async def async_step_add_from_template(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a template to pre-fill the add tool form."""
        if user_input is not None:
            template_key = user_input["template"]
            self._template_data = TOOL_TEMPLATES[template_key]
            return await self.async_step_add_tool()

        template_options = {
            key: tmpl["label"] for key, tmpl in TOOL_TEMPLATES.items()
        }

        return self.async_show_form(
            step_id="add_from_template",
            data_schema=vol.Schema(
                {
                    vol.Required("template"): vol.In(template_options),
                }
            ),
        )

    async def _test_tool_code(
        self, code: str, test_params_raw: str
    ) -> str:
        """Compile and execute tool code, return formatted result string."""
        error_hint = "\n\nFix the code below and try again."
        try:
            params = json.loads(test_params_raw) if test_params_raw else {}
        except json.JSONDecodeError:
            return f"**Error:**\n```\nInvalid test parameters JSON\n```{error_hint}"

        try:
            indented = textwrap.indent(code, "    ")
            wrapped = f"async def _test_fn(hass, params):\n{indented}\n"
            namespace: dict[str, Any] = {}
            exec(compile(wrapped, "<test>", "exec"), namespace)  # noqa: S102
            fn = namespace["_test_fn"]
        except SyntaxError:
            return f"**Error:**\n```\n{traceback.format_exc()}\n```{error_hint}"

        try:
            result = await asyncio.wait_for(
                fn(self.hass, params), timeout=10
            )
            formatted = json.dumps(result, default=str, ensure_ascii=False, indent=2)
        except asyncio.TimeoutError:
            formatted = "Execution timed out (10s limit)"
            return f"**Error:**\n```\n{formatted}\n```{error_hint}"
        except Exception:  # noqa: BLE001
            return f"**Error:**\n```\n{traceback.format_exc()}\n```{error_hint}"

        if len(formatted) > 2000:
            formatted = formatted[:2000] + "\n... (truncated)"
        ok_hint = "\n\nSubmit again to save, or modify the code below."
        return f"**Result:**\n```\n{formatted}\n```{ok_hint}"

    def _build_tool_schema(
        self,
        name: str = "",
        description: str = "",
        params_json: str = "",
        code: str = "",
        test_params: str = "",
        *,
        include_delete: bool = False,
    ) -> vol.Schema:
        """Build the add/edit tool form schema with given defaults."""
        schema_dict: dict[vol.Marker, Any] = {
            vol.Required("tool_name", default=name): str,
            vol.Required("tool_description", default=description): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Optional("tool_params", default=params_json): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Required("tool_code", default=code): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Optional("test_only", default=False): BooleanSelector(),
            vol.Optional("test_params", default=test_params): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
        }
        if include_delete:
            schema_dict[vol.Optional("delete", default=False)] = BooleanSelector()
        return vol.Schema(schema_dict)

    async def async_step_add_tool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a custom tool."""
        errors: dict[str, str] = {}
        test_result = ""

        # Consume template prefill on first entry
        prefill = self._template_data
        if prefill:
            self._template_data = None

        if user_input is not None:
            name = user_input["tool_name"].strip()
            description = user_input["tool_description"].strip()
            code = user_input["tool_code"].strip()
            params_json = user_input.get("tool_params", "").strip()
            is_test = user_input.get("test_only", False)
            test_params_raw = user_input.get("test_params", "").strip()

            if not name:
                errors["tool_name"] = "name_required"
            elif any(t["name"] == name for t in self._custom_tools):
                errors["tool_name"] = "name_exists"

            if not code:
                errors["tool_code"] = "code_required"

            if params_json:
                try:
                    json.loads(params_json)
                except json.JSONDecodeError:
                    errors["tool_params"] = "invalid_json"

            if not errors:
                try:
                    indented = textwrap.indent(code, "    ")
                    wrapped = f"async def _t(hass, params):\n{indented}\n"
                    exec(compile(wrapped, "<validate>", "exec"), {})  # noqa: S102
                except SyntaxError:
                    errors["tool_code"] = "syntax_error"

            if is_test and not errors:
                test_result = await self._test_tool_code(code, test_params_raw)
                return self.async_show_form(
                    step_id="add_tool",
                    data_schema=self._build_tool_schema(
                        name, description, params_json, code, test_params_raw,
                    ),
                    errors=errors,
                    description_placeholders={"test_result": test_result},
                )

            if not errors:
                new_tool: dict[str, Any] = {
                    "id": generate_tool_id(),
                    "name": name,
                    "description": description,
                    "params_json": params_json or "{}",
                    "code": code,
                }
                self._custom_tools.append(new_tool)
                return self.async_create_entry(
                    data={
                        **self._get_current_settings(),
                        "custom_tools": self._custom_tools,
                    }
                )

        # Pre-fill from template or show blank form
        if prefill:
            schema = self._build_tool_schema(
                prefill["name"],
                prefill["description"],
                prefill["params_json"],
                prefill["code"],
            )
        else:
            schema = self._build_tool_schema()

        return self.async_show_form(
            step_id="add_tool",
            data_schema=schema,
            errors=errors,
            description_placeholders={"test_result": test_result},
        )

    async def async_step_edit_tool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit or delete an existing custom tool."""
        tool = next(
            (t for t in self._custom_tools if t["id"] == self._editing_tool_id),
            None,
        )
        if tool is None:
            return self.async_create_entry(
                data={
                    **self._get_current_settings(),
                    "custom_tools": self._custom_tools,
                }
            )

        errors: dict[str, str] = {}
        test_result = ""

        if user_input is not None:
            # Delete
            if user_input.get("delete"):
                self._custom_tools = [
                    t
                    for t in self._custom_tools
                    if t["id"] != self._editing_tool_id
                ]
                return self.async_create_entry(
                    data={
                        **self._get_current_settings(),
                        "custom_tools": self._custom_tools,
                    }
                )

            # Edit — validate
            name = user_input["tool_name"].strip()
            description = user_input["tool_description"].strip()
            code = user_input["tool_code"].strip()
            params_json = user_input.get("tool_params", "").strip()
            is_test = user_input.get("test_only", False)
            test_params_raw = user_input.get("test_params", "").strip()

            if not name:
                errors["tool_name"] = "name_required"
            elif name != tool["name"] and any(
                t["name"] == name for t in self._custom_tools
            ):
                errors["tool_name"] = "name_exists"

            if not code:
                errors["tool_code"] = "code_required"

            if params_json:
                try:
                    json.loads(params_json)
                except json.JSONDecodeError:
                    errors["tool_params"] = "invalid_json"

            if not errors:
                try:
                    indented = textwrap.indent(code, "    ")
                    wrapped = f"async def _t(hass, params):\n{indented}\n"
                    exec(compile(wrapped, "<validate>", "exec"), {})  # noqa: S102
                except SyntaxError:
                    errors["tool_code"] = "syntax_error"

            if is_test and not errors:
                test_result = await self._test_tool_code(code, test_params_raw)
                return self.async_show_form(
                    step_id="edit_tool",
                    data_schema=self._build_tool_schema(
                        name, description, params_json, code, test_params_raw,
                        include_delete=True,
                    ),
                    errors=errors,
                    description_placeholders={"test_result": test_result},
                )

            if not errors:
                tool["name"] = name
                tool["description"] = description
                tool["params_json"] = params_json or "{}"
                tool["code"] = code
                return self.async_create_entry(
                    data={
                        **self._get_current_settings(),
                        "custom_tools": self._custom_tools,
                    }
                )

        return self.async_show_form(
            step_id="edit_tool",
            data_schema=self._build_tool_schema(
                tool["name"],
                tool["description"],
                tool.get("params_json", "{}"),
                tool["code"],
                include_delete=True,
            ),
            errors=errors,
            description_placeholders={"test_result": test_result},
        )

    def _get_current_settings(self) -> dict[str, Any]:
        """Get current non-tool settings."""
        return {
            CONF_RESPONSE_TIMEOUT: self.config_entry.options.get(
                CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT
            ),
            CONF_MCP_URL: self.config_entry.options.get(CONF_MCP_URL, ""),
        }
