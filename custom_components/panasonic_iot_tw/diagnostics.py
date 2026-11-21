"""Diagnostics support for the Panasonic IoT TW integration."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .api_constants import API_ENDPOINTS
from .coordinator import PanasonicConfigEntry

# Keys that carry credentials or personally identifying information. This must
# cover both the config-entry data/options and the nested device dicts stored in
# the coordinator (both the processed keys and the raw API keys inside
# ``raw_data``). Key matching in ``async_redact_data`` is case sensitive, so the
# raw API spellings (e.g. ``GWID``, ``Auth``, ``LatLng``) are listed explicitly.
TO_REDACT = {
    # Config entry credentials.
    "username",
    "password",
    # Client statistics (SmartApp.get_statistics).
    "account",
    "device_ids",
    # Processed device identifiers.
    "device_id",
    "auth",
    "gwid",
    # Raw API identifiers / credentials.
    "Auth",
    "GWID",
    "GWID2",
    "CPToken",
    "cptoken",
    "RefreshToken",
    "refreshtoken",
    "refresh_token",
    "cp_token",
    # Location fields.
    "LatLng",
    "latlng",
    "lat",
    "lng",
    "Lat",
    "Lng",
    "City",
    "city",
    "Area",
    "area",
}


# This is intentionally a temporary, read-only investigation tool for
# NA-LX128B and other Type 3 washer-dryer combos. It does not expose raw
# UserGetDeviceStatus responses, which may contain credentials or identifiers.
_OVERVIEW_SENSITIVE_KEY = re.compile(
    r"auth|token|gwid|password|account|email|phone|tel|address|location|"
    r"serial|uuid|imei|mac|member|(?:user|device)[_-]?id|name|nickname|"
    r"(?:^|_)ip(?:$|_)|lat|lng",
    re.IGNORECASE,
)
_OVERVIEW_SAFE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}\Z")
_OVERVIEW_NUMBER = re.compile(r"-?\d{1,6}(?:\.\d{1,3})?\Z")
_OVERVIEW_TIME = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\Z")
_OVERVIEW_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+Z-]{3,30})?\Z"
)
_OVERVIEW_HEX_COMMAND = re.compile(r"0[xX][0-9A-Fa-f]{1,4}\Z")
_OVERVIEW_STATE_WORDS = {
    "success", "ok", "standby", "running", "drying", "washing",
    "finished", "reserved", "error", "idle", "true", "false",
    "on", "off", "stop", "start", "normal", "unknown", "offline",
    "online", "complete", "completed", "waiting", "ready",
}

# Overview's CommandType contains a short hexadecimal register address,
# such as "0x05"; unlike arbitrary free text, this is safe to report.
# Status is deliberately limited to small numeric, date/time, and known
# status words; unknown text (including possible identifiers) stays masked.
_OVERVIEW_SCALAR_FIELDS = {"AreaID", "SeqNo", "Time", "Status", "CommandType"}
_OVERVIEW_LIST_LIMIT = 128


def _safe_overview_shape(
    value: Any, depth: int = 0, field: str = ""
) -> Any:
    """Bounded overview snapshot with strict field-level value allowlists."""
    if depth >= 7:
        return "<depth limit>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= 35:
                result["<other fields>"] = len(value) - index
                break
            safe_key = (
                key
                if isinstance(key, str) and _OVERVIEW_SAFE_KEY.fullmatch(key)
                else f"field_{index}"
            )
            if _OVERVIEW_SENSITIVE_KEY.search(str(key)):
                result[safe_key] = "<redacted>"
            else:
                result[safe_key] = _safe_overview_shape(
                    child, depth + 1, str(key)
                )
        return result
    if isinstance(value, list):
        # The register List can contain many command codes. Previously
        # showing only 5 entries concealed potentially relevant values.
        limit = _OVERVIEW_LIST_LIMIT if field == "List" else 5
        sample = [
            _safe_overview_shape(item, depth + 1, field)
            for item in value[:limit]
        ]
        if len(value) > limit:
            sample.append(f"<{len(value) - limit} more items>")
        return sample
    if isinstance(value, bool) or value is None:
        return value if field in _OVERVIEW_SCALAR_FIELDS else "<scalar>"
    if field == "CommandType":
        if isinstance(value, str) and _OVERVIEW_HEX_COMMAND.fullmatch(value):
            return value
        return "<unrecognized-command>"
    if field not in _OVERVIEW_SCALAR_FIELDS:
        return "<text>"
    if isinstance(value, (int, float)):
        return value if abs(value) <= 100000 else "<large number>"
    if isinstance(value, str):
        if (
            _OVERVIEW_NUMBER.fullmatch(value)
            or _OVERVIEW_TIME.fullmatch(value)
            or _OVERVIEW_DATE.fullmatch(value)
            or value.lower() in _OVERVIEW_STATE_WORDS
        ):
            return value
        return "<text>"
    return f"<{type(value).__name__}>"


async def _probe_laundry_overview(coordinator: Any) -> dict[str, Any]:
    """On-demand GET for the alternate overview endpoint, no control calls."""
    app = coordinator.smart_app
    try:
        await app._token_manager.ensure_authenticated()
        headers = {
            **app._token_manager.get_auth_headers(),
            "apptype": "Smart",
        }
        response = await app._api_client.request(
            method="GET",
            endpoint=API_ENDPOINTS["get_device_overview"],
            headers=headers,
            log=False,
        )
        return {
            "result": "ok",
            "endpoint": "UserGetDeviceStatus (GET, read-only)",
            "overview_shape": _safe_overview_shape(response),
        }
    except Exception as exc:
        # Error messages may include API payloads or device names: only the
        # exception type is safe to put in downloadable diagnostics.
        return {"result": "error", "error_type": type(exc).__name__}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PanasonicConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    # The coordinator data is keyed by device_id (a credential-derived
    # identifier). async_redact_data only redacts values, not mapping keys, so
    # re-key by a synthetic index to avoid leaking the id while keeping a dict
    # shape and preserving every device (the redacted device dict still carries
    # its non-sensitive ``nickname`` for identification).
    redacted_devices = {
        f"device_{index}": async_redact_data(device, TO_REDACT)
        for index, device in enumerate((coordinator.data or {}).values())
    }

    update_interval = coordinator.update_interval

    # An explicit diagnostics download is the only trigger for this probe.
    # Do not add cloud API traffic to normal polling or other device types.
    has_laundry_device = any(
        device.get("device_type") == 3
        for device in (coordinator.data or {}).values()
    )
    overview_probe = (
        await _probe_laundry_overview(coordinator)
        if has_laundry_device
        else None
    )

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": (
                update_interval.total_seconds()
                if update_interval is not None
                else None
            ),
        },
        # Client-side statistics: token/refresh state, request counts and
        # report-cache ages. Helps triage auth and rate-limit issues from a
        # diagnostics download alone.
        "client": async_redact_data(coordinator.smart_app.get_statistics(), TO_REDACT),
        "data": redacted_devices,
        **({"overview_probe": overview_probe} if overview_probe is not None else {}),
    }
