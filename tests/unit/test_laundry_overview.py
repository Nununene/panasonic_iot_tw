"""Tests for NA-LX128B read-only status overview decoding and ETA caching."""
from datetime import datetime, timedelta, timezone

from custom_components.panasonic_iot_tw.laundry_overview import (
    OVERVIEW_FRESHNESS,
    attach_laundry_overview,
    current_expected_finish,
    current_remaining_minutes,
    decode_overview,
    summarize_overview_response,
)

START = datetime(2026, 11, 21, 4, 34, tzinfo=timezone.utc)


def _device_data():
    return {
        "laundry": {
            "device_type": 3, "gwid": "washer-gw",
            "device_id": "device-1", "available": True,
            "status": {"0x50": "2", "0x05": "-1683.3"},
        },
        "fridge": {
            "device_type": 2, "gwid": "fridge-gw", "available": True,
        },
    }


def _payload(statuses):
    return {
        "GwList": [
            {"GWID": "washer-gw", "List": [
                {"CommandType": code, "Status": value}
                for code, value in statuses.items()
            ]},
            {"GWID": "fridge-gw", "List": [
                {"CommandType": "0x41", "Status": "123"}
            ]},
        ],
    }


def test_decoding_keeps_independent_registers_without_empty_as_zero():
    readings = decode_overview(_payload({
        "0x41": "", "0x4F": None, "0x50": "2",
        "0x54": "6", "0x20": "", "0xFF": "800",
    }))
    assert readings["washer-gw"] == {"0x50": 2, "0x54": 6}
    assert readings["fridge-gw"]["0x41"] == 123


def test_timed_observations_match_app_finish_at_1425_taipei():
    # 12:34 Taipei = 04:34 UTC; App ETA 14:25 Taipei = 06:25 UTC.
    data = attach_laundry_overview(
        _device_data(), None,
        _payload({
            "0x41": "111", "0x4F": "111",
            "0x50": "2", "0x54": "6", "0x20": "66",
        }), START,
    )
    device = data["laundry"]
    assert device["overview_status"]["0x41"] == 111
    assert device["overview_status"]["0x4F"] == 111
    assert device["overview_status"]["0x20"] == 66
    assert current_expected_finish(device, START) == START + timedelta(minutes=111)
    assert current_remaining_minutes(device, START) == 111
    assert "overview_status" not in data["fridge"]


def test_second_observation_six_minutes_later_keeps_same_eta():
    first = attach_laundry_overview(
        _device_data(), None,
        _payload({
            "0x41": "111", "0x4F": "111",
            "0x50": "2", "0x54": "6", "0x20": "66",
        }), START,
    )
    later = START + timedelta(minutes=6)
    second = attach_laundry_overview(
        _device_data(), first,
        _payload({
            "0x41": "105", "0x4F": "105",
            "0x50": "2", "0x54": "6", "0x20": "66",
        }), later,
    )
    assert current_expected_finish(second["laundry"], later) == (
        START + timedelta(minutes=111)
    )
    assert current_remaining_minutes(second["laundry"], later) == 105


def test_partial_empty_status_preserves_each_last_valid_field_independently():
    first = attach_laundry_overview(
        _device_data(), None,
        _payload({
            "0x41": "105", "0x4F": "105",
            "0x50": "2", "0x54": "6", "0x20": "66",
        }), START,
    )
    later = START + timedelta(minutes=3)
    data = attach_laundry_overview(
        _device_data(), first,
        _payload({
            "0x41": "", "0x4F": "", "0x50": "2",
            "0x54": "6", "0x20": "",
        }), later,
    )
    device = data["laundry"]
    assert device["overview_status"] == {
        "0x41": 105, "0x4F": 105,
        "0x50": 2, "0x54": 6, "0x20": 66,
    }
    assert device["overview_observed_at"]["0x41"] == START.isoformat()
    assert device["overview_observed_at"]["0x54"] == later.isoformat()
    assert current_expected_finish(device, later) == (
        START + timedelta(minutes=105)
    )
    assert current_remaining_minutes(device, later) == 102


def test_all_empty_status_does_not_reset_recent_eta():
    first = attach_laundry_overview(
        _device_data(), None,
        _payload({"0x41": "111", "0x4F": "111", "0x50": "2"}),
        START,
    )
    later = START + timedelta(minutes=5)
    data = attach_laundry_overview(
        _device_data(), first,
        _payload({"0x41": "", "0x4F": "", "0x50": ""}),
        later,
    )
    assert current_remaining_minutes(data["laundry"], later) == 106


def test_timeout_discards_cached_values_and_eta():
    first = attach_laundry_overview(
        _device_data(), None,
        _payload({"0x41": "111", "0x4F": "111", "0x50": "2"}),
        START,
    )
    now = START + OVERVIEW_FRESHNESS + timedelta(seconds=1)
    data = attach_laundry_overview(_device_data(), first, None, now)
    assert data["laundry"]["overview_status"] == {}
    assert data["laundry"]["overview_expected_finish_at"] is None
    assert current_remaining_minutes(data["laundry"], now) is None


def test_stopped_appliance_clears_eta_even_if_minutes_old_values_remain():
    first = attach_laundry_overview(
        _device_data(), None,
        _payload({"0x41": "105", "0x4F": "105", "0x50": "2"}),
        START,
    )
    now = START + timedelta(minutes=1)
    data = attach_laundry_overview(
        _device_data(), first, _payload({"0x50": "5"}), now,
    )
    assert data["laundry"]["overview_status"]["0x50"] == 5
    assert current_remaining_minutes(data["laundry"], now) is None


def test_4f_nanoe_time_is_never_a_whole_programme_eta_even_during_drying():
    # Cloud model metadata: 0x4F = nanoe殘時間, not total or phase ETA.
    data = attach_laundry_overview(
        _device_data(), None,
        _payload({
            "0x41": "", "0x4F": "85", "0x50": "2", "0x54": "6",
        }),
        START,
    )
    assert data["laundry"]["overview_status"]["0x4F"] == 85
    assert data["laundry"]["overview_eta_command"] is None
    assert current_expected_finish(data["laundry"], START) is None


def test_old_nanoe_derived_eta_cannot_survive_new_observation():
    prior = attach_laundry_overview(
        _device_data(), None,
        _payload({"0x41": "120", "0x50": "2", "0x54": "6"}),
        START,
    )
    old = prior["laundry"]
    old["overview_eta_command"] = "0x4F"
    next_minute = START + timedelta(minutes=1)
    next_data = attach_laundry_overview(
        _device_data(), prior,
        _payload({"0x41": "", "0x4F": "60", "0x50": "2", "0x54": "6"}),
        next_minute,
    )
    assert current_expected_finish(next_data["laundry"], next_minute) is None
    assert next_data["laundry"]["overview_eta_command"] is None


def test_0x41_schema_allows_up_to_1440_minutes():
    data = attach_laundry_overview(
        _device_data(), None,
        _payload({"0x41": "750", "0x4F": "11", "0x50": "2"}),
        START,
    )
    assert current_remaining_minutes(data["laundry"], START) == 750
    assert data["laundry"]["overview_eta_command"] == "0x41"


def test_spin_phase_4f_does_not_override_missing_full_cycle_41():
    # Actual cycle: 15:02 spin had 0x41=116, 0x4F=11.
    # CommandList now confirms that 0x4F is nanoe time, not spin time.
    data = attach_laundry_overview(
        _device_data(), None,
        _payload({
            "0x41": "", "0x4F": "11", "0x50": "2", "0x54": "5",
        }),
        START,
    )
    assert data["laundry"]["overview_eta_command"] is None
    assert current_expected_finish(data["laundry"], START) is None


def test_time_codes_disagree_in_spin_prefers_full_programme_41():
    data = attach_laundry_overview(
        _device_data(), None,
        _payload({
            "0x41": "116", "0x4F": "11", "0x50": "2",
            "0x54": "5", "0x20": "66",
        }),
        START,
    )
    assert data["laundry"]["overview_eta_command"] == "0x41"
    assert current_remaining_minutes(data["laundry"], START) == 116
    assert current_expected_finish(data["laundry"], START) == (
        START + timedelta(minutes=116)
    )


def test_4f_without_fresh_phase_code_cannot_supply_full_cycle_eta():
    # Even if the cached stage used to be drying, the latest 0x54 might
    # have been omitted or empty while the programme changed phases.
    data = attach_laundry_overview(
        _device_data(), None,
        _payload({"0x41": "", "0x4F": "11", "0x50": "2"}),
        START,
    )
    assert data["laundry"]["overview_eta_command"] is None
    assert current_remaining_minutes(data["laundry"], START) is None


def test_invalid_sentinels_and_negative_remaining_minutes_are_ignored():
    data = attach_laundry_overview(
        _device_data(), None,
        _payload({
            "0x41": "-1683.3", "0x4F": "-1", "0x50": "2",
            "0x54": "6",
        }),
        START,
    )
    assert current_remaining_minutes(data["laundry"], START) is None



def test_diagnostic_summary_distinguishes_blank_absent_valid_and_invalid():
    """Cloud Status fields can be independently blank in the same List."""
    sample = _payload({
        "0x41": "", "0x50": "2", "0x54": "6",
        "0x20": "-1683.3",
        # 0x4F entirely omitted from List.
    })
    result = summarize_overview_response(sample, "washer-gw")
    assert result == {
        "result": "matched",
        "commands": {
            "0x41": "empty_string",
            "0x4F": "not_returned",
            "0x50": "numeric",
            "0x54": "numeric",
            "0x20": "invalid_value",
        },
    }
    assert "washer-gw" not in str(result)
    assert "-1683.3" not in str(result)
    assert "Status" not in str(result)


def test_diagnostic_summary_missing_status_field_and_unknown_gateway():
    sample = {
        "GwList": [
            {"GWID": "SECRET_DEVICE_ID", "List": [
                {"CommandType": "0x41"},
                {"CommandType": "0x4F", "Status": "75"},
            ]}
        ]
    }
    summary = summarize_overview_response(sample, "SECRET_DEVICE_ID")
    assert summary["commands"]["0x41"] == "status_field_missing"
    assert summary["commands"]["0x4F"] == "numeric"
    assert "SECRET_DEVICE_ID" not in str(summary)
    assert summarize_overview_response(
        sample, "DIFFERENT_ID"
    )["result"] == "gateway_not_returned"


def test_diagnostic_summary_invalid_gateway_payload_safe():
    assert summarize_overview_response(None, "id")["result"] == "invalid_response"
    assert summarize_overview_response({"GwList": None}, "id")["result"] == (
        "invalid_gateway_list"
    )
    assert summarize_overview_response({
        "GwList": [{"GWID": "id", "List": None}]
    }, "id")["result"] == "invalid_command_list"
