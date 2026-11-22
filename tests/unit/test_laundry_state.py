"""Tests for tentative NA-LX128B programme-state interpretation.

Samples originate from the user's 2026-11-21 HA/API + official app observations.
The vendor has not documented the meanings of these registers.
"""
from datetime import datetime, timedelta, timezone

from custom_components.panasonic_iot_tw.laundry_state import (
    laundry_operation_state, laundry_state_attributes,
)

BASE = datetime(2026, 11, 21, 5, 24, 25, tzinfo=timezone.utc)


def _device(power, operation, stage=None, *, moment=BASE, model="NA-LX128B"):
    device = {
        "model": model,
        "status": {"0x01": str(power), "0x50": str(operation)},
        "overview_status": {},
        "overview_observed_at": {},
        "overview_last_response_summary": None,
        "overview_last_response_at": None,
    }
    if stage is not None:
        device["overview_status"] = {"0x50": 2, "0x54": stage}
        device["overview_observed_at"] = {
            "0x50": moment.isoformat(),
            "0x54": moment.isoformat(),
        }
        device["overview_last_response_at"] = moment.isoformat()
        device["overview_last_response_summary"] = {
            "result": "matched",
            "commands": {"0x50": "numeric", "0x54": "numeric"},
        }
    return device


def test_direct_stage_and_operation_override_conflicting_overview():
    # In a successful direct poll, 0x50 and 0x54 take precedence over
    # another endpoint even if the Overview codes are also numeric.
    device = _device(1, 2, 3)
    device["available"] = True
    device["status"].update({"0x54": "5"})
    device["status_observed_at"] = BASE.isoformat()
    device["status_is_cached"] = False
    assert laundry_operation_state(device, BASE) == "spinning"
    attrs = laundry_state_attributes(device, BASE)
    assert attrs["effective_0x50_source"] == "DeviceGetInfo"
    assert attrs["effective_0x54_source"] == "DeviceGetInfo"


def test_direct_missing_stage_falls_back_to_fresh_overview():
    device = _device(1, 2, 4)
    device["available"] = True
    device["status"]["0x54"] = "-1683.3"
    device["status_observed_at"] = BASE.isoformat()
    device["status_is_cached"] = False
    assert laundry_operation_state(device, BASE) == "rinsing"
    assert laundry_state_attributes(device, BASE)[
        "effective_0x54_source"
    ] == "UserGetDeviceStatus"


def test_cached_device_info_does_not_override_newer_overview_phase():
    device = _device(1, 2, 4)
    device["available"] = True
    device["status"]["0x54"] = "5"
    device["status_observed_at"] = BASE.isoformat()
    device["status_is_cached"] = True
    assert laundry_operation_state(device, BASE) == "rinsing"


def test_direct_standby_prevents_overview_running_phase():
    device = _device(1, 1, 3)
    device["available"] = True
    device["status"]["0x54"] = "5"
    device["status_observed_at"] = BASE.isoformat()
    device["status_is_cached"] = False
    assert laundry_operation_state(device, BASE) == "standby"


def test_overview_can_drive_state_when_direct_status_unavailable():
    device = _device(1, 2, 3)
    device["available"] = False
    device["status"] = {}
    device["status_observed_at"] = None
    device["status_is_cached"] = False
    assert laundry_operation_state(device, BASE) == "washing"
    assert laundry_state_attributes(device, BASE)[
        "effective_0x50_source"
    ] == "UserGetDeviceStatus"


def test_stale_direct_and_expired_overview_do_not_report_running():
    device = _device(1, 2, 3)
    device["available"] = True
    device["status"]["0x54"] = "3"
    device["status_observed_at"] = (
        BASE - timedelta(minutes=6)
    ).isoformat()
    device["status_is_cached"] = True
    assert laundry_operation_state(device, BASE + timedelta(minutes=6)) is None


def test_washing_phase_3_from_wash_only_app_and_cloud_metadata():
    # At 20:24:24 Taipei on 2026-11-21, the App showed 洗衣中 in
    # wash-only; same successful overview returned 0x50=2 and 0x54=3.
    # The model's CommandList also declares 0x54=3 as 洗衣.
    device = _device(1, 2, 3)
    device["status"].update({"0x55": "0", "0x72": "8192", "0x73": "1"})
    assert laundry_operation_state(device, BASE) == "washing"
    assert laundry_state_attributes(device, BASE)["overview_phase_current"] is True

    # Blank current 0x54 must not resurrect a cached washing label.
    device["overview_last_response_summary"]["commands"]["0x54"] = "empty_string"
    assert laundry_operation_state(device, BASE) == "running"


def test_rinse_phase_4_from_wash_only_app_and_cloud_metadata():
    # 2026-11-21 20:30:32 Taipei: wash-only official App said 洗清中,
    # concurrent overview 0x50=2/0x54=4 were both new numeric readings.
    # LX128B CommandList also labels phase 4 洗清.
    device = _device(1, 2, 4)
    device["status"].update({"0x55": "0", "0x72": "8192", "0x73": "1"})
    assert laundry_operation_state(device, BASE) == "rinsing"
    assert laundry_state_attributes(device, BASE)["overview_phase_current"] is True

    # A cached 0x54=4 must not be treated as the current rinse stage.
    next_poll = BASE + timedelta(minutes=3)
    device["overview_last_response_at"] = next_poll.isoformat()
    device["overview_last_response_summary"]["commands"]["0x54"] = "empty_string"
    assert laundry_operation_state(device, next_poll) == "running"


def test_drying_inferred_only_from_synchronized_valid_stage_6():
    device = _device(1, 2, 6)
    assert laundry_operation_state(device, BASE) == "drying"
    assert laundry_state_attributes(device, BASE)["overview_phase_current"] is True


def test_spinning_phase_5_from_real_wash_and_dry_sample():
    """14:56:11 overview matched official app spinning at 14:59."""
    sample = _device(1, 2, 5)
    sample["status"]["0x74"] = "0"
    assert laundry_operation_state(sample, BASE) == "spinning"
    assert laundry_state_attributes(sample, BASE)["overview_phase_current"] is True

    # This should not claim spinning from an older cached 0x54=5
    sample["overview_last_response_summary"]["commands"]["0x54"] = "empty_string"
    assert laundry_operation_state(sample, BASE) == "running"


def test_anti_crease_holding_phase_11():
    device = _device(1, 2, 11)
    assert laundry_operation_state(device, BASE) == "soft_keeping"


def test_paused_register_7_precedes_stale_or_strange_stage_49601():
    device = _device(1, 7, 49601)
    assert laundry_operation_state(device, BASE) == "paused"


def test_real_paused_sample_01_2_with_new_numeric_overview_50_7():
    # 2026-11-21 16:28:13: App says 一時停止中, HA incorrectly said off.
    # DeviceGetInfo 0x01=2, 0x50=7. Overview 0x50=7 is fresh, but
    # 0x54=11 is cached from 16:25:09 and missing in latest response.
    device = _device(2, 7)
    device["overview_status"] = {"0x50": 7, "0x54": 11}
    device["overview_observed_at"] = {
        "0x50": BASE.isoformat(),
        "0x54": (BASE - timedelta(minutes=3)).isoformat(),
    }
    device["overview_last_response_at"] = BASE.isoformat()
    device["overview_last_response_summary"] = {
        "result": "matched",
        "commands": {
            "0x41": "empty_string", "0x4F": "empty_string",
            "0x50": "numeric", "0x54": "empty_string",
            "0x20": "empty_string",
        },
    }
    assert laundry_operation_state(device, BASE) == "paused"
    attrs = laundry_state_attributes(device, BASE)
    assert attrs["overview_0x50_live_classification"] == "numeric"
    assert attrs["overview_phase_current"] is False


def test_power_off_stale_7_with_latest_blank_cloud_50():
    # 13:36:40 after power-off: DeviceGetInfo 0x01=2, 0x50=7,
    # but latest overview returns all empty. Cached 7 is NOT fresh.
    device = _device(2, 7)
    device["overview_status"] = {"0x50": 7, "0x54": 49601}
    device["overview_observed_at"] = {
        "0x50": (BASE - timedelta(minutes=3)).isoformat(),
        "0x54": (BASE - timedelta(minutes=3)).isoformat(),
    }
    device["overview_last_response_at"] = BASE.isoformat()
    device["overview_last_response_summary"] = {
        "result": "matched",
        "commands": {
            "0x50": "empty_string", "0x54": "empty_string",
        },
    }
    assert laundry_operation_state(device, BASE) == "off"
    assert laundry_state_attributes(
        device, BASE
    )["overview_0x50_live_classification"] == "empty_string"


def test_ambiguous_power_2_without_live_overview_does_not_claim_off():
    device = _device(2, 7)
    assert laundry_operation_state(device, BASE) is None
    # Successful summary alone does not suffice if its timestamp is missing.
    device["overview_last_response_summary"] = {
        "result": "matched", "commands": {"0x50": "numeric"},
    }
    device["overview_status"] = {"0x50": 7}
    assert laundry_operation_state(device, BASE) is None


def test_cached_numeric_7_from_older_poll_is_not_live_pause():
    device = _device(2, 7)
    device["overview_status"] = {"0x50": 7}
    device["overview_observed_at"] = {
        "0x50": (BASE - timedelta(minutes=3)).isoformat()
    }
    device["overview_last_response_at"] = BASE.isoformat()
    device["overview_last_response_summary"] = {
        "result": "matched", "commands": {"0x50": "numeric"},
    }
    assert laundry_operation_state(device, BASE) is None


def test_old_successful_overview_cannot_claim_pause_or_off():
    device = _device(2, 7)
    device["overview_status"] = {"0x50": 7}
    device["overview_observed_at"] = {"0x50": BASE.isoformat()}
    device["overview_last_response_at"] = BASE.isoformat()
    device["overview_last_response_summary"] = {
        "result": "matched", "commands": {"0x50": "numeric"},
    }
    assert laundry_operation_state(device, BASE + timedelta(minutes=6)) is None


def test_standby_after_power_on_ignores_cached_paused_stage():
    # Standby read: DeviceGetInfo 0x01=3, 0x50=1, while 0x54 still 49601.
    device = _device(3, 1, 49601)
    assert laundry_operation_state(device, BASE) == "standby"


def test_blank_latest_phase_response_ignores_cache_from_older_read():
    device = _device(1, 2, 11)
    next_poll = BASE + timedelta(minutes=3)
    device["overview_last_response_at"] = next_poll.isoformat()
    device["overview_last_response_summary"]["commands"]["0x54"] = "empty_string"
    assert laundry_operation_state(device, next_poll) == "running"
    assert not laundry_state_attributes(
        device, next_poll
    )["overview_phase_current"]


def test_phase_must_be_at_the_same_time_as_overview_operation():
    device = _device(1, 2, 11)
    device["overview_observed_at"]["0x54"] = (
        BASE - timedelta(minutes=1)
    ).isoformat()
    assert laundry_operation_state(device, BASE) == "running"


def test_unavailable_overview_summary_keeps_general_running():
    device = _device(1, 2, 6)
    device["overview_last_response_summary"] = None
    assert laundry_operation_state(device, BASE) == "running"


def test_old_cloud_sample_downgrades_to_general_running():
    device = _device(1, 2, 11)
    assert laundry_operation_state(
        device, BASE + timedelta(minutes=6)
    ) == "running"


def test_unknown_54_does_not_claim_drying():
    device = _device(1, 2, 49601)
    assert laundry_operation_state(device, BASE) == "running"


def test_unsupported_device_status_is_unknown_not_invented():
    device = _device(3, 8)
    assert laundry_operation_state(device, BASE) is None


def test_other_models_are_not_classified_using_na_lx128b_codes():
    device = _device(1, 2, 11, model="NA-OTHER")
    assert laundry_operation_state(device, BASE) is None


def test_fresh_phase_requires_numeric_overview_50():
    device = _device(1, 2, 11)
    device["overview_status"]["0x50"] = 7
    assert laundry_operation_state(device, BASE) == "running"


def test_power_2_without_paused_operation_is_not_automatically_off():
    device = _device(2, 5)
    device["overview_last_response_at"] = BASE.isoformat()
    device["overview_last_response_summary"] = {
        "result": "matched",
        "commands": {"0x50": "empty_string", "0x54": "empty_string"},
    }
    assert laundry_operation_state(device, BASE) is None


def test_state_attributes_include_raw_age_with_no_private_gateway_id():
    device = _device(3, 1, 49601)
    attrs = laundry_state_attributes(device, BASE)
    assert attrs["device_get_info_0x01"] == "3"
    assert attrs["device_get_info_0x50"] == "1"
    assert attrs["overview_0x54"] == 49601
    assert attrs["overview_0x54_observed_at"] == BASE.isoformat()
    assert attrs["overview_phase_current"] is False
    assert "gwid" not in attrs
