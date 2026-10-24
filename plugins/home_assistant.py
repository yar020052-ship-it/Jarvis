"""Home Assistant integration for JARVIS.

Connects to the user's configured Home Assistant instance over its REST API.
The plugin only exposes a small allowlist of service calls; it never accepts an
arbitrary URL, endpoint, or service name from the model.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request

from memory.config_manager import get_plugin_config

_NS = "home_assistant"
_TIMEOUT = 12

_SERVICES = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "input_boolean": {"turn_on", "turn_off", "toggle"},
    "scene": {"turn_on"},
    "script": {"turn_on"},
    "climate": {"set_temperature", "set_hvac_mode", "turn_on", "turn_off"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position"},
    "fan": {"turn_on", "turn_off", "set_percentage"},
    "lock": {"lock", "unlock"},
    "alarm_control_panel": {"arm_home", "arm_away", "arm_night", "disarm"},
    "media_player": {"turn_on", "turn_off", "media_play_pause", "volume_set",
                     "volume_up", "volume_down", "media_next_track", "media_previous_track"},
}
_ALLOWED_DATA = {
    "brightness", "brightness_pct", "color_temp_kelvin", "rgb_color",
    "transition", "temperature", "hvac_mode", "position", "percentage",
    "volume_level", "entity_id", "code",
}
_SENSITIVE = {
    ("lock", "unlock"), ("alarm_control_panel", "disarm"),
    ("alarm_control_panel", "arm_home"), ("alarm_control_panel", "arm_away"),
    ("alarm_control_panel", "arm_night"), ("cover", "open_cover"),
    ("cover", "close_cover"), ("script", "turn_on"),
}


def _config() -> tuple[str, str]:
    values = get_plugin_config(_NS)
    return str(values.get("url") or "").strip().rstrip("/"), str(values.get("token") or "").strip()


def _base_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Укажите адрес Home Assistant, например http://homeassistant.local:8123")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Адрес не должен содержать логин, пароль, query или fragment.")
    # Do not send a long-lived token over cleartext to a public host.
    if parts.scheme == "http":
        host = parts.hostname.lower()
        local = host in {"localhost", "homeassistant"} or host.endswith(".local")
        try:
            local = local or ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if not local:
            raise ValueError("Для удалённого Home Assistant используйте HTTPS; HTTP разрешён только для локальной сети.")
    return url


def _request(method: str, path: str, body: dict | None = None,
             url: str | None = None, token: str | None = None):
    if url is None or token is None:
        cfg_url, cfg_token = _config()
        url = url or cfg_url
        token = token or cfg_token
    if not url or not token:
        raise ValueError("Сначала задайте URL и Long-Lived Access Token в настройках плагина Home Assistant.")
    base = _base_url(url)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/" + path.lstrip("/"), data=data, method=method,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
            raw = response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        detail = exc.read(600).decode("utf-8", "replace")
        if exc.code == 401:
            raise ValueError("Home Assistant отклонил токен (401). Проверьте Long-Lived Access Token.") from exc
        raise ValueError(f"Home Assistant вернул HTTP {exc.code}: {detail[:300]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"Не удалось подключиться к Home Assistant: {exc}") from exc
    if len(raw) > 2_000_000:
        raise ValueError("Ответ Home Assistant слишком большой; сузьте запрос.")
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", "replace")


def _test_connection(values: dict):
    try:
        result = _request("GET", "", url=str(values.get("url") or "").strip().rstrip("/"),
                          token=str(values.get("token") or "").strip())
        version = result.get("version", "connected") if isinstance(result, dict) else "connected"
        return True, f"Подключено к Home Assistant ({version})."
    except Exception as exc:
        return False, str(exc)


PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "🏠  HOME ASSISTANT",
    "fields": [
        {"key": "url", "label": "Home Assistant URL", "type": "text",
         "placeholder": "http://homeassistant.local:8123"},
        {"key": "token", "label": "Long-Lived Access Token", "type": "password",
         "placeholder": "Профиль Home Assistant → Security → Long-Lived Access Tokens"},
    ],
    "action": {"label": "ПРОВЕРИТЬ ПОДКЛЮЧЕНИЕ", "run": _test_connection},
}


def _confirm(title: str, detail: str) -> str:
    try:
        from core import confirm
        return str(confirm.request_and_wait(
            "home_assistant", title, detail,
            run=lambda: "__home_assistant_approved__",
        ))
    except Exception:
        return "Не удалось запросить подтверждение через JARVIS. Ничего не выполнено."


def _states() -> list[dict]:
    value = _request("GET", "states")
    if not isinstance(value, list):
        raise ValueError("Home Assistant вернул неожиданный список состояний.")
    return [row for row in value if isinstance(row, dict)]


def _describe_states(rows: list[dict], query: str = "", limit: int = 60) -> str:
    q = query.casefold().strip()
    matched = []
    for row in rows:
        entity = str(row.get("entity_id") or "")
        attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        name = str(attrs.get("friendly_name") or entity)
        domain = entity.split(".", 1)[0] if "." in entity else ""
        if domain not in _SERVICES and domain not in {"sensor", "binary_sensor", "person", "device_tracker", "weather"}:
            continue
        if q and q not in (entity + " " + name).casefold():
            continue
        state = str(row.get("state") or "unknown")
        unit = str(attrs.get("unit_of_measurement") or "")
        matched.append(f"• {name} ({entity}): {state}{(' ' + unit) if unit else ''}")
    if not matched:
        return "Устройств по этому запросу не найдено." if q else "Home Assistant не вернул доступных устройств."
    return "Устройства Home Assistant:\n" + "\n".join(matched[:limit])


def _service_call(domain: str, service: str, entity_id: str, data: dict) -> str:
    safe_data = {k: v for k, v in data.items() if k in _ALLOWED_DATA and k != "entity_id"}
    payload = dict(safe_data)
    if entity_id:
        payload["entity_id"] = entity_id
    result = _request("POST", f"services/{domain}/{service}", payload)
    count = len(result) if isinstance(result, list) else 0
    return f"Выполнено: {domain}.{service}" + (f" для {entity_id}" if entity_id else "") + (f"; HA обновил {count} объект(ов)." if count else ".")


def run(parameters: dict, player=None, session_memory=None) -> str:
    """List states or perform a constrained, user-requested HA service call."""
    p = parameters if isinstance(parameters, dict) else {}
    action = str(p.get("action") or "list").strip().lower()
    try:
        if action in {"status", "list", "search"}:
            query = str(p.get("query") or "")[:120]
            return _describe_states(_states(), query)
        if action == "services":
            return "Разрешённые действия: " + "; ".join(
                f"{domain}: {', '.join(sorted(services))}" for domain, services in _SERVICES.items()
            )
        if action != "call_service":
            return "Доступно: status/list/search, services, call_service."
        domain = str(p.get("domain") or "").strip().lower()
        service = str(p.get("service") or "").strip().lower()
        entity_id = str(p.get("entity_id") or "").strip()
        data = p.get("data") if isinstance(p.get("data"), dict) else {}
        if domain not in _SERVICES or service not in _SERVICES[domain]:
            return "Эта служба не разрешена. Запросите список разрешённых services."
        if entity_id and (len(entity_id) > 180 or not all(part for part in entity_id.split(".", 1))):
            return "Некорректный entity_id."
        # Confirm that the target exists before sending a write to HA.
        states = _states()
        known = {str(row.get("entity_id")) for row in states}
        if domain not in {"scene", "script"} and not entity_id:
            return "Для этой команды укажите entity_id конкретного устройства."
        if entity_id and any(e not in known for e in entity_id.split(",")):
            return "Не найден target entity_id в Home Assistant; сначала запросите список устройств."
        action_text = f"{domain}.{service}" + (f" для {entity_id}" if entity_id else "")
        if (domain, service) in _SENSITIVE:
            answer = _confirm("Управление Home Assistant", f"Разрешить выполнить {action_text}?")
            if answer != "__home_assistant_approved__":
                return answer or "Операция отменена."
        return _service_call(domain, service, entity_id, data)
    except Exception as exc:
        return f"Home Assistant: {exc}"


PLUGIN = {
    "name": _NS,
    "description": (
        "Controls the user's configured Home Assistant installation. Use this for smart-home requests: "
        "list/search devices and their current states, or call an explicitly allowlisted service for lights, "
        "switches, scenes, climate, covers, fans, locks, alarms, and media players. First use status/search "
        "to discover the exact entity_id. Pass action=call_service, domain, service, entity_id, and only the "
        "relevant allowed service data. Sensitive lock, alarm, cover, and script actions require JARVIS HUD "
        "confirmation. Connection URL and bearer token are read only from local plugin settings."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["status", "list", "search", "services", "call_service"],
                       "description": "status/list/search lists devices; services lists supported service calls; call_service controls a device."},
            "query": {"type": "STRING", "description": "Optional name/entity substring for listing devices."},
            "domain": {"type": "STRING", "description": "Home Assistant domain, e.g. light, climate, cover."},
            "service": {"type": "STRING", "description": "Allowlisted service, e.g. turn_on, set_temperature."},
            "entity_id": {"type": "STRING", "description": "Exact entity_id discovered through status/search; comma-separated targets are accepted."},
            "data": {"type": "OBJECT", "description": "Optional allowlisted service fields such as brightness_pct, temperature, hvac_mode, position, percentage, volume_level, or rgb_color."},
        },
        "required": ["action"],
    },
}
