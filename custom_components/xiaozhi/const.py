"""Constants for the Xiaozhi AI Conversation integration."""

DOMAIN = "xiaozhi"

# Config keys
CONF_SERVER_URL = "server_url"
CONF_ACCESS_TOKEN = "access_token"
CONF_DEVICE_ID = "device_id"
CONF_CLIENT_ID = "client_id"
CONF_PROTOCOL_VERSION = "protocol_version"
CONF_RESPONSE_TIMEOUT = "response_timeout"
CONF_MCP_URL = "mcp_url"

# Defaults
CLOUD_SERVER_URL = "wss://api.tenclass.net/xiaozhi/v1/"
DEFAULT_PROTOCOL_VERSION = 3
DEFAULT_RESPONSE_TIMEOUT = 30
MIN_RESPONSE_TIMEOUT = 5
MAX_RESPONSE_TIMEOUT = 120

# OTA
OTA_URL = "https://api.tenclass.net/xiaozhi/ota/"
OTA_POLL_INTERVAL = 3
OTA_TIMEOUT = 300

# Message types
MSG_TYPE_HELLO = "hello"
MSG_TYPE_LISTEN = "listen"
MSG_TYPE_STT = "stt"
MSG_TYPE_TTS = "tts"
MSG_TYPE_ABORT = "abort"
MSG_TYPE_MCP = "mcp"
MSG_TYPE_LLM = "llm"

# Listen states
LISTEN_STATE_DETECT = "detect"
LISTEN_STATE_START = "start"
LISTEN_STATE_STOP = "stop"

# TTS states
TTS_STATE_START = "start"
TTS_STATE_STOP = "stop"
TTS_STATE_SENTENCE_START = "sentence_start"

# STT states
STT_STATE_START = "start"
STT_STATE_STOP = "stop"

# Audio
AUDIO_SAMPLE_RATE_INPUT = 16000
AUDIO_SAMPLE_RATE_OUTPUT = 24000
AUDIO_CHANNELS = 1
AUDIO_FRAME_DURATION_MS = 60
BINARY_FRAME_TYPE_AUDIO = 0

# Pipeline cache
PIPELINE_CACHE_TTL = 30

# Pipeline timeouts (seconds)
STT_RESULT_TIMEOUT = 30
PIPELINE_COLLECT_TIMEOUT = 60

# Supported languages (shared by STT and TTS entities)
SUPPORTED_LANGUAGES = ["zh", "en", "ru", "ja", "ko", "fr", "de", "es", "it", "pt"]

# OTA application constants
APP_VERSION = "0.1.0"
OTA_BOARD_TYPE = "ha-integration"
OTA_BOARD_NAME = "HomeAssistant"
OTA_DEFAULT_TIMEOUT_MS = 300000

# Reconnection
RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 60
RECONNECT_BACKOFF_FACTOR = 2
