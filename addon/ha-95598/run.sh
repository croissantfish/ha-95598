#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${CONFIG_PATH:-/data/options.json}"
if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Add-on options not found at ${CONFIG_PATH}" >&2
  exit 1
fi

umask 077
OPTIONS_ENV="$(mktemp)"
trap 'rm -f "${OPTIONS_ENV}"' EXIT
python3 - "${CONFIG_PATH}" > "${OPTIONS_ENV}" <<'PY'
import json
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"Invalid add-on options in {path}: {exc}") from exc
if not isinstance(data, dict):
    raise SystemExit(f"Invalid add-on options in {path}: expected a JSON object.")

options = (
    ("account", "ACCOUNT", ""),
    ("password", "PASSWORD", ""),
    ("ignore_user_id", "IGNORE_USER_ID", ""),
    ("mqtt_host", "MQTT_HOST", "core-mosquitto"),
    ("mqtt_port", "MQTT_PORT", 1883),
    ("mqtt_username", "MQTT_USERNAME", ""),
    ("mqtt_password", "MQTT_PASSWORD", ""),
    ("mqtt_publish_timeout_seconds", "MQTT_PUBLISH_TIMEOUT_SECONDS", 10),
    ("job_start_time", "JOB_START_TIME", "07:00"),
    ("job_times", "JOB_TIMES", 2),
    ("fetch_on_startup", "FETCH_ON_STARTUP", False),
    ("retry_times_limit", "RETRY_TIMES_LIMIT", 3),
    ("retry_wait_time_offset_unit", "RETRY_WAIT_TIME_OFFSET_UNIT", 5),
    ("republish_interval_minutes", "REPUBLISH_INTERVAL_MINUTES", 0),
    ("fetch_attempt_timeout_minutes", "FETCH_ATTEMPT_TIMEOUT_MINUTES", 30),
    ("daily_usage_window_days", "DAILY_USAGE_WINDOW_DAYS", 7),
    ("publish_tou_detail_sensors", "PUBLISH_TOU_DETAIL_SENSORS", False),
    ("tou_price_config", "TOU_PRICE_CONFIG", "config/tou_price_config.json"),
    ("notifier", "NOTIFIER", "none"),
    ("tg_bot_token", "TG_BOT_TOKEN", ""),
    ("tg_chat_id", "TG_CHAT_ID", ""),
    ("tg_api_base_url", "TG_API_BASE_URL", "https://api.telegram.org"),
    ("stale_data_alert_days", "STALE_DATA_ALERT_DAYS", 2),
    ("history_gap_alert_days", "HISTORY_GAP_ALERT_DAYS", 30),
    ("captcha_point_click_max_refreshes", "CAPTCHA_POINT_CLICK_MAX_REFRESHES", 4),
    ("login_fallback", "LOGIN_FALLBACK", "qrcode"),
    ("allow_qr_fallback_after_rk001", "ALLOW_QR_FALLBACK_AFTER_RK001", False),
    ("qr_code_login_wait_count", "QR_CODE_LOGIN_WAIT_COUNT", 7),
    ("qr_code_login_refresh_limit", "QR_CODE_LOGIN_REFRESH_LIMIT", 1),
    ("trace_retention_days", "TRACE_RETENTION_DAYS", 7),
    ("page_trace_mode", "PAGE_TRACE_MODE", "errors"),
    ("browser_profile_cache_max_mb", "BROWSER_PROFILE_CACHE_MAX_MB", 128),
)
for key, environment_name, default in options:
    value = data.get(key, default)
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif value is None:
        value = ""
    print(f"export {environment_name}={shlex.quote(str(value))}")

login_credentials = data.get("login_credentials", [])
if login_credentials:
    serialized = json.dumps(login_credentials, ensure_ascii=False)
    print(f"export LOGIN_CREDENTIALS={shlex.quote(serialized)}")
PY

source "${OPTIONS_ENV}"
rm -f "${OPTIONS_ENV}"
trap - EXIT

export BROWSER_HEADLESS=true
unset DISPLAY

if [[ "$(id -u)" -eq 0 ]]; then
  chown -R app:app /app/data
  exec gosu app python3 -m scripts.main
fi
exec python3 -m scripts.main
