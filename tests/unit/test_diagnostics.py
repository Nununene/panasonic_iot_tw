"""Unit tests for the Panasonic IoT TW diagnostics."""
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.panasonic_iot_tw.const import CONF_PROXY, CONF_UPDATE_INTERVAL
from custom_components.panasonic_iot_tw.diagnostics import (
    async_get_config_entry_diagnostics,
)

REDACTED = "**REDACTED**"


def _entry_with_coordinator():
    """Build a config entry whose runtime_data is a coordinator with data."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.update_interval = timedelta(seconds=180)
    coordinator.data = {
        "device_auth_1": {
            "device_id": "device_auth_1",
            "auth": "device_auth_1",
            "gwid": "gateway_id_1",
            "nickname": "Living Room AC",
            "model": "CS-K25YA2",
            "device_type": 1,
            "available": True,
            "status": {"0x00": 1},
            "raw_data": {
                "Auth": "device_auth_1",
                "GWID": "gateway_id_1",
                "NickName": "Living Room AC",
                "DeviceType": "1",
                "Model": "CS-K25YA2",
                "LatLng": "25.03,121.56",
                "City": "Taipei",
                "Area": "Xinyi",
            },
        }
    }

    coordinator.smart_app.get_statistics.return_value = {
        "account": "user@example.com",
        "operation_count": 42,
        "last_successful_update": "2026-08-18T10:00:00+00:00",
        "cached_devices": 1,
        "token_info": {
            "is_authenticated": True,
            "is_token_expired": False,
            "login_count": 1,
            "refresh_count": 3,
        },
        "api_stats": {"total_requests": 99, "cached_devices": 1},
        "cache_stats": {"cached_devices": 1, "device_ids": ["device_auth_1"]},
        "report_cache_stats": {"energy_devices": 1},
    }

    entry = Mock()
    entry.data = {"username": "user@example.com", "password": "secret"}
    entry.options = {CONF_PROXY: "http://proxy:8080", CONF_UPDATE_INTERVAL: 180}
    entry.runtime_data = coordinator
    return entry


async def test_diagnostics_structure():
    """Diagnostics return the expected top-level structure."""
    entry = _entry_with_coordinator()

    result = await async_get_config_entry_diagnostics(Mock(), entry)

    assert set(result) == {"entry", "coordinator", "client", "data"}
    assert set(result["entry"]) == {"data", "options"}
    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["update_interval"] == 180.0
    # Client statistics come through with non-sensitive fields intact.
    assert result["client"]["token_info"]["refresh_count"] == 3
    assert result["client"]["api_stats"]["total_requests"] == 99
    # Non-sensitive fields survive.
    assert result["data"]["device_0"]["nickname"] == "Living Room AC"
    assert result["data"]["device_0"]["model"] == "CS-K25YA2"
    assert result["entry"]["options"][CONF_PROXY] == "http://proxy:8080"


async def test_diagnostics_redacts_credentials():
    """Credentials and identifiers never appear verbatim in the output."""
    entry = _entry_with_coordinator()

    result = await async_get_config_entry_diagnostics(Mock(), entry)

    dumped = str(result)
    for secret in (
        "secret",
        "user@example.com",
        "gateway_id_1",
        "device_auth_1",
        "25.03,121.56",
    ):
        assert secret not in dumped, f"{secret!r} leaked into diagnostics"

    # Specific redactions on both processed and raw keys.
    device = result["data"]["device_0"]
    assert device["gwid"] == REDACTED
    assert device["auth"] == REDACTED
    assert device["device_id"] == REDACTED
    assert device["raw_data"]["GWID"] == REDACTED
    assert device["raw_data"]["Auth"] == REDACTED
    assert device["raw_data"]["LatLng"] == REDACTED
    assert device["raw_data"]["City"] == REDACTED
    assert result["entry"]["data"]["password"] == REDACTED
    assert result["entry"]["data"]["username"] == REDACTED
    assert result["client"]["account"] == REDACTED
    assert result["client"]["cache_stats"]["device_ids"] == REDACTED


async def test_laundry_overview_probe_is_read_only_and_redacted():
    """An explicit diagnostics download can compare the alternate endpoint."""
    entry = _entry_with_coordinator()
    coordinator = entry.runtime_data
    coordinator.data["device_auth_1"]["device_type"] = 3
    app = coordinator.smart_app
    app._token_manager.ensure_authenticated = AsyncMock()
    app._token_manager.get_auth_headers.return_value = {"cptoken": "SENSITIVE_TOKEN"}
    app._api_client.request = AsyncMock(return_value={
        "status": "success",
        "devices": [{
            "GWID": "PRIVATE_GATEWAY",
            "Auth": "PRIVATE_AUTH",
            "NickName": "Private owner",
            "DeviceID": "12345678",
            "RemainingTime": "62",
            "EstimatedFinish": "12:11",
            "Status": "drying",
            "description": "secret private message",
        }],
    })

    result = await async_get_config_entry_diagnostics(Mock(), entry)

    assert result["overview_probe"]["result"] == "ok"
    shape = result["overview_probe"]["overview_shape"]
    assert shape["status"] == "success"
    assert shape["devices"][0]["RemainingTime"] == "62"
    assert shape["devices"][0]["EstimatedFinish"] == "12:11"
    assert shape["devices"][0]["GWID"] == "<redacted>"
    assert shape["devices"][0]["Auth"] == "<redacted>"
    assert shape["devices"][0]["NickName"] == "<redacted>"
    assert shape["devices"][0]["DeviceID"] == "<redacted>"
    assert shape["devices"][0]["description"] == "<text>"
    dumped = str(result)
    for secret in ("SENSITIVE_TOKEN", "PRIVATE_GATEWAY", "PRIVATE_AUTH", "Private owner", "secret private message"):
        assert secret not in dumped
    assert app._api_client.request.await_count == 2
    kwargs = app._api_client.request.await_args_list[0].kwargs
    assert kwargs["method"] == "GET"
    assert kwargs["endpoint"].endswith("/UserGetDeviceStatus")
    assert kwargs["log"] is False
    second = app._api_client.request.await_args_list[1].kwargs
    assert second["method"] == "GET"
    assert second["endpoint"].endswith("/UserGetRegisteredGwList2")
    assert second["log"] is False


async def test_laundry_overview_probe_suppresses_error_text():
    """An HTTP error can include private response text; exclude it."""
    entry = _entry_with_coordinator()
    coordinator = entry.runtime_data
    coordinator.data["device_auth_1"]["device_type"] = 3
    app = coordinator.smart_app
    app._token_manager.ensure_authenticated = AsyncMock()
    app._token_manager.get_auth_headers.return_value = {"cptoken": "SENSITIVE_TOKEN"}
    app._api_client.request = AsyncMock(side_effect=ValueError("PRIVATE_AUTH"))

    result = await async_get_config_entry_diagnostics(Mock(), entry)

    assert result["overview_probe"] == {"result": "error", "error_type": "ValueError"}
    assert "PRIVATE_AUTH" not in str(result)



async def test_overview_register_codes_are_visible_but_identifiers_stay_hidden():
    """Preserve valid short command codes and numeric states across full List."""
    entry = _entry_with_coordinator()
    coordinator = entry.runtime_data
    coordinator.data["device_auth_1"]["device_type"] = 3
    app = coordinator.smart_app
    app._token_manager.ensure_authenticated = AsyncMock()
    app._token_manager.get_auth_headers.return_value = {
        "cptoken": "HIDDEN_CP_TOKEN"
    }
    info = [
        {"CommandType": f"0x{code:02X}", "Status": str(code)}
        for code in range(12)
    ]
    info[5]["Status"] = "-1683.3"
    info[10]["Status"] = "SECRET_TOKEN_ABC_12345"
    info.append({"CommandType": "SECRET_COMMAND_ID", "Status": "SECRET_PASSWORD"})
    app._api_client.request = AsyncMock(return_value={
        "GwList": [{
            "GWID": "PRIVATE_GATEWAY",
            "List": info,
        }],
    })

    result = await async_get_config_entry_diagnostics(Mock(), entry)
    sample = result["overview_probe"]["overview_shape"]["GwList"][0]["List"]

    assert len(sample) == len(info), "Diagnostic output must not stop at 5 codes"
    assert sample[5] == {"CommandType": "0x05", "Status": "-1683.3"}
    assert sample[6] == {"CommandType": "0x06", "Status": "6"}
    assert sample[10] == {"CommandType": "0x0A", "Status": "<redacted-sensitive-status>"}
    assert sample[-1] == {
        "CommandType": "<unrecognized-command>",
        "Status": "<redacted-sensitive-status>",
    }
    dumped = str(result)
    assert "PRIVATE_GATEWAY" not in dumped
    assert "HIDDEN_CP_TOKEN" not in dumped
    assert "SECRET_TOKEN" not in dumped
    assert "SECRET_PASSWORD" not in dumped
    assert "SECRET_COMMAND_ID" not in dumped



async def test_overview_status_text_relaxed_without_private_data():
    """Non-numeric appliance states remain visible; secrets still blocked."""
    entry = _entry_with_coordinator()
    coordinator = entry.runtime_data
    coordinator.data["device_auth_1"]["device_type"] = 3
    app = coordinator.smart_app
    app._token_manager.ensure_authenticated = AsyncMock()
    app._token_manager.get_auth_headers.return_value = {"cptoken": "secret"}
    app._api_client.request = AsyncMock(return_value={
        "GwList": [{
            "GWID": "MY_PRIVATE_GWID",
            "List": [
                {"CommandType": "0x41", "Status": "drying in progress"},
                {"CommandType": "0x50", "Status": "--"},
                {"CommandType": "0x54", "Status": "-1683.3"},
                {"CommandType": "0x20", "Status": "2026/11/21 12:11"},
                {"CommandType": "0x4F", "Status": "user@example.com"},
                {"CommandType": "0x05", "Status": "Bearer PRIVATE_TOKEN"},
                {"CommandType": "0x0A", "Status": "0xA0 FF 00: 11"},
            ],
        }],
    })

    result = await async_get_config_entry_diagnostics(Mock(), entry)
    statuses = {
        item["CommandType"]: item["Status"]
        for item in result["overview_probe"]["overview_shape"]["GwList"][0]["List"]
    }
    assert statuses["0x41"] == "drying in progress"
    assert statuses["0x50"] == "--"
    assert statuses["0x54"] == "-1683.3"
    assert statuses["0x20"] == "2026/11/21 12:11"
    assert statuses["0x4F"] == "<redacted-sensitive-status>"
    assert statuses["0x05"] == "<redacted-sensitive-status>"
    assert statuses["0x0A"] == "0xA0 FF 00: 11"
    assert "user@example.com" not in str(result)
    assert "PRIVATE_TOKEN" not in str(result)
    assert "MY_PRIVATE_GWID" not in str(result)



async def test_laundry_command_list_probe_parses_metadata_and_redacts_identifiers():
    """Parse JSON and direct List command metadata for only matching laundries."""
    entry = _entry_with_coordinator()
    coordinator = entry.runtime_data
    coordinator.data["device_auth_1"]["device_type"] = 3
    app = coordinator.smart_app
    app._token_manager.ensure_authenticated = AsyncMock()
    app._token_manager.get_auth_headers.return_value = {
        "cptoken": "MY_SECRET_TOKEN"
    }
    response = {
        "GwList": [
            {"GWID": "REAL_PRIVATE_GWID", "Auth": "REAL_AUTH", "DeviceType": "3", "ModelType": "LaundryType"},
            {"GWID": "OTHER_GWID", "DeviceType": "1", "ModelType": "AC"},
        ],
        "CommandList": [
            {"DeviceType": "1", "ModelType": "AC",
             "List": [{"CommandType": "0x99"}]},
            {"DeviceType": "3", "ModelType": "LaundryType", "JSON":
                '{"List": [{"CommandType": "0x05", "Private": "secret"}, {"CommandType": "0x13"}]}'},
            {"DeviceType": "3", "ModelType": "WrongType",
             "List": [{"CommandType": "0x88"}]},
            {"DeviceType": "3", "ModelType": "LaundryType",
             "List": [{"CommandType": "0x34"}, {"CommandType": "secret:token"}]},
        ],
    }
    app._api_client.request = AsyncMock(side_effect=[
        {"GwList": []}, response,
    ])

    result = await async_get_config_entry_diagnostics(Mock(), entry)
    data = result["command_list_probe"]
    assert data["result"] == "ok"
    assert data["laundry_device_count"] == 1
    assert data["command_list_entry_count"] == 4
    assert data["matched_entry_count"] == 2
    assert data["matching_entries"][0]["command_codes"] == ["0x05", "0x13"]
    assert data["matching_entries"][1]["command_codes"] == ["0x34"]
    assert "Private" in data["matching_entries"][0]["command_item_keys"]
    dumped = str(result)
    for secret in ("REAL_PRIVATE_GWID", "REAL_AUTH", "MY_SECRET_TOKEN", "WrongType", "secret:token"):
        assert secret not in dumped


async def test_command_list_probe_failure_does_not_break_diagnostics():
    entry = _entry_with_coordinator()
    coordinator = entry.runtime_data
    coordinator.data["device_auth_1"]["device_type"] = 3
    app = coordinator.smart_app
    app._token_manager.ensure_authenticated = AsyncMock()
    app._token_manager.get_auth_headers.return_value = {"cptoken": "private"}
    app._api_client.request = AsyncMock(side_effect=[
        {"GwList": []}, RuntimeError("private auth leaked"),
    ])
    result = await async_get_config_entry_diagnostics(Mock(), entry)
    assert result["command_list_probe"] == {
        "result": "error", "error_type": "RuntimeError",
    }
    assert "private auth leaked" not in str(result)
