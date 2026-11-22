"""Unit tests for washing machine device implementation."""
import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import UnitOfTime
from homeassistant.helpers.entity import EntityCategory

from custom_components.panasonic_iot_tw.devices.washing_machine import WashingMachineDevice
from custom_components.panasonic_iot_tw.const import (
    WASHING_MACHINE_AVAILABLE_STATUS,
    WASHING_MACHINE_AVAILABLE_CYCLES
)


class TestWashingMachineDevice:
    """Test cases for WashingMachineDevice class."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator with washing machine data."""
        coordinator = Mock()
        coordinator.data = {
            0: {
                "status": {
                    "0x13": 45,   # Washing remaining time: 45 minutes
                    "0x15": 3,    # Schedule remaining time: 3 hours
                    "0x34": 208,  # Engineering info: binary 11010000 (pre_wash=128, washing=64, rinse=16)
                    "0x50": 2,    # Operation status: 2 (running)
                    "0x55": 1,    # Cycle message: 1
                    "0x64": 1088, # Engineering info ext: binary 10001000000 (pre_wash_ext=1024, washing_ext=64)
                    "0x74": 1,    # Remote control allowed: 1
                }
            }
        }
        coordinator.last_update_success = True
        return coordinator

    @pytest.fixture
    def mock_device_data(self):
        """Create mock device data."""
        return {
            "device_id": "washing_machine_test_001",
            "nickname": "測試洗衣機",
            "model": "Test Washing Machine Model",
            "version": "1.0.0",
            "available": True,
            "device_type": 4,  # Assuming 4 is washing machine type
        }

    @pytest.fixture
    def washing_machine_device(self, mock_coordinator, mock_device_data):
        """Create a WashingMachineDevice instance for testing."""
        return WashingMachineDevice(mock_coordinator, 0, mock_device_data)

    def test_device_initialization(self, washing_machine_device, mock_device_data):
        """Test proper device initialization."""
        assert washing_machine_device.device_key == 0
        assert washing_machine_device._device_data == mock_device_data
        assert washing_machine_device.device_id == "washing_machine_test_001"
        assert washing_machine_device.model == "Test Washing Machine Model"

    def test_command_constants(self):
        """Test that all command constants are properly defined."""
        assert WashingMachineDevice.WASHING_REMAINING_TIME_COMMAND == "0x13"
        assert WashingMachineDevice.SCHEDULE_REMAINING_TIME_COMMAND == "0x15"
        assert WashingMachineDevice.ENGINEERING_INFO_COMMAND == "0x34"
        assert WashingMachineDevice.OPERATION_STATUS_COMMAND == "0x50"
        assert WashingMachineDevice.CYCLE_MESSAGE_COMMAND == "0x55"
        assert WashingMachineDevice.REMOTE_CONTROL_COMMAND == "0x74"

    def test_get_sensor_entities_count(self, washing_machine_device, mock_coordinator):
        """Test that get_sensor_entities returns correct number of sensors."""
        sensors = washing_machine_device.get_sensor_entities(mock_coordinator)
        assert len(sensors) == 19  # Existing washer sensors + ETA and five overview registers

    def test_na_lx128b_has_extra_experimental_operation_state(
        self, mock_coordinator, mock_device_data
    ):
        """Only the tested combo model gets the additional enum entity."""
        mock_device_data["model"] = "NA-LX128B"
        mock_coordinator.data[0]["model"] = "NA-LX128B"
        mock_coordinator.data[0]["status"]["0x01"] = "3"
        mock_coordinator.data[0]["status"]["0x50"] = "1"
        device = WashingMachineDevice(mock_coordinator, 0, mock_device_data)
        sensors = device.get_sensor_entities(mock_coordinator)
        assert len(sensors) == 15  # LX128B: 10 supported + 5 raw Overview diagnostics
        state = next(
            sensor for sensor in sensors
            if sensor._attr_unique_id.endswith("_laundry_operation_state")
        )
        assert state.device_class == SensorDeviceClass.ENUM
        assert state._attr_unique_id == (
            "washing_machine_test_001_laundry_operation_state"
        )
        assert state.native_value == "standby"
        assert state.extra_state_attributes["device_get_info_0x01"] == "3"

        # The exact DeviceGetInfo pair 0x01=2, 0x50=7 occurs in
        # BOTH paused and powered-off states. Without a recent overview,
        # return unknown instead of incorrectly declaring it off.
        mock_coordinator.data[0]["status"]["0x01"] = "2"
        mock_coordinator.data[0]["status"]["0x50"] = "7"
        assert state.native_value is None

        # The power-off observation had an empty *fresh* overview 0x50,
        # with any old cached value no longer valid as a live indication.
        now = datetime.now(timezone.utc).isoformat()
        mock_coordinator.data[0]["overview_last_response_at"] = now
        mock_coordinator.data[0]["overview_last_response_summary"] = {
            "result": "matched",
            "commands": {"0x50": "empty_string"},
        }
        assert state.native_value == "off"

        # Paused sample instead returned fresh numeric overview 0x50=7.
        mock_coordinator.data[0]["overview_last_response_summary"] = {
            "result": "matched",
            "commands": {"0x50": "numeric"},
        }
        mock_coordinator.data[0]["overview_status"] = {"0x50": 7}
        mock_coordinator.data[0]["overview_observed_at"] = {"0x50": now}
        assert state.native_value == "paused"

        # A same-read 0x54=3 in an active NA-LX128B wash-only
        # programme should be exposed as the localized washing enum.
        mock_coordinator.data[0]["status"]["0x01"] = "1"
        mock_coordinator.data[0]["status"]["0x50"] = "2"
        mock_coordinator.data[0]["overview_last_response_summary"] = {
            "result": "matched",
            "commands": {"0x50": "numeric", "0x54": "numeric"},
        }
        mock_coordinator.data[0]["overview_status"] = {"0x50": 2, "0x54": 3}
        mock_coordinator.data[0]["overview_observed_at"] = {
            "0x50": now, "0x54": now
        }
        assert "washing" in state.options
        assert state.native_value == "washing"

        # In the same wash-only cycle the official App then reported
        # 洗清中, corroborating fresh overview 0x54=4.
        mock_coordinator.data[0]["overview_status"]["0x54"] = 4
        assert "rinsing" in state.options
        assert state.native_value == "rinsing"

        # Successful latest Overview can keep this experimental sensor
        # available after DeviceGetInfo becomes temporarily unavailable.
        mock_coordinator.data[0]["status"] = {}
        mock_coordinator.data[0]["available"] = False
        mock_coordinator.data[0]["status_observed_at"] = None
        mock_coordinator.data[0]["status_is_cached"] = False
        assert state.available is True
        assert state.native_value == "rinsing"

        # Neither source now reports live 0x50: do not claim availability.
        mock_coordinator.data[0]["overview_last_response_summary"] = {
            "result": "matched",
            "commands": {"0x50": "empty_string", "0x54": "empty_string"},
        }
        assert state.available is False


    def test_lx128b_drops_legacy_unsupported_and_duplicate_sensors(
        self, mock_coordinator, mock_device_data
    ):
        mock_device_data["model"] = "NA-LX128B"
        mock_coordinator.data[0]["model"] = "NA-LX128B"
        device = WashingMachineDevice(mock_coordinator, 0, mock_device_data)
        sensors = device.get_sensor_entities(mock_coordinator)
        ids = {e.unique_id.rsplit("washing_machine_test_001_", 1)[-1]
               for e in sensors}
        assert "programme_remaining_time" in ids
        assert "programme_expected_finish" in ids
        assert "laundry_operation_state" in ids
        assert "cycle_message" in ids
        assert "detergent_amount" in ids
        assert "softener_amount" in ids
        assert "error_code" in ids
        assert {f"overview_{code}" for code in ("41", "4f", "50", "54", "20")} <= ids
        # Only NA-LX128B has a new programme-time source. Do not
        # register the old 0x13, 0x34, 0x05 and duplicate 0x50 entities.
        for removed in (
            "washing_remaining_time", "current_stage",
            "schedule_remaining_time", "operation_status",
            "drying_remaining_time", "drying_expected_finish",
            "drying_error_code",
        ):
            assert removed not in ids

        remaining = next(
            e for e in sensors
            if e.unique_id.endswith("_programme_remaining_time")
        )
        expected = next(
            e for e in sensors
            if e.unique_id.endswith("_programme_expected_finish")
        )
        assert remaining._command_type == "0x41"
        assert remaining._data_source == "overview_remaining"
        assert expected._command_type == "0x41"
        assert expected._data_source == "overview_eta"

        # No unsupported 0x34 phase binaries, but keep the dispenser
        # shortage warnings and remote control status.
        binaries = device.get_binary_sensor_entities(mock_coordinator)
        assert len(binaries) == 3
        binary_ids = {e.unique_id for e in binaries}
        assert any(x.endswith("_detergent_low") for x in binary_ids)
        assert any(x.endswith("_softener_low") for x in binary_ids)
        assert any(x.endswith("_remote_control_allowed") for x in binary_ids)


    def test_get_sensor_entities_types(self, washing_machine_device, mock_coordinator):
        """Test that sensor entities have correct types and properties."""
        with patch('custom_components.panasonic_iot_tw.sensor.PanasonicSensor') as mock_sensor_class:
            mock_sensor_instance = Mock()
            mock_sensor_class.return_value = mock_sensor_instance

            sensors = washing_machine_device.get_sensor_entities(mock_coordinator)

            # Should create 19 sensors
            assert mock_sensor_class.call_count == 19

            # Check specific sensor configurations
            calls = mock_sensor_class.call_args_list

            # First call - washing remaining time
            args, kwargs = calls[0]
            assert kwargs['command_type'] == "0x13"
            assert kwargs['sensor_key'] == "washing_remaining_time"
            assert kwargs['device_class'] == SensorDeviceClass.DURATION
            assert kwargs['unit'] == UnitOfTime.MINUTES

            # Second call - schedule remaining time
            args, kwargs = calls[1]
            assert kwargs['command_type'] == "0x15"
            assert kwargs['sensor_key'] == "schedule_remaining_time"
            assert kwargs['device_class'] == SensorDeviceClass.DURATION
            assert kwargs['unit'] == UnitOfTime.HOURS

            # Third call - operation status
            args, kwargs = calls[2]
            assert kwargs['command_type'] == "0x50"
            assert kwargs['sensor_key'] == "operation_status"

            # Fourth call - cycle message
            args, kwargs = calls[3]
            assert kwargs['command_type'] == "0x55"
            assert kwargs['sensor_key'] == "cycle_message"

            # Fifth call - current stage, gated on the operation status
            args, kwargs = calls[4]
            assert kwargs['command_type'] == "0x34"
            assert kwargs['sensor_key'] == "current_stage"
            assert kwargs['gate_command'] == "0x50"
            assert kwargs['gate_allowed'] == (2, 5)

            # Sixth and seventh - how much each dispenser slot dispenses
            args, kwargs = calls[5]
            assert kwargs['command_type'] == "0x72"
            assert kwargs['sensor_key'] == "detergent_amount"
            assert kwargs['value_processor'](512) == "normal"
            assert kwargs['value_processor'](0) == "none"

            args, kwargs = calls[6]
            assert kwargs['command_type'] == "0x72"
            assert kwargs['sensor_key'] == "softener_amount"
            assert kwargs['value_processor'](3) == "more"
            assert kwargs['value_processor'](512) == "none"

            # Existing unique ID preserved but source is now the validated
            # account-level overview, not DeviceGetInfo register 0x05.
            assert calls[11][1]["command_type"] == "0x41"
            assert calls[11][1]["sensor_key"] == "drying_remaining_time"
            assert calls[11][1]["unit"] == UnitOfTime.MINUTES
            assert calls[11][1]["data_source"] == "overview_remaining"
            assert "gate_command" not in calls[11][1]
            assert calls[12][1]["command_type"] == "0x0A"
            assert calls[12][1]["sensor_key"] == "drying_error_code"
            assert calls[13][1]["sensor_key"] == "drying_expected_finish"
            assert calls[13][1]["device_class"] == SensorDeviceClass.TIMESTAMP
            assert calls[13][1]["data_source"] == "overview_eta"
            assert [call[1]["command_type"] for call in calls[14:19]] == [
                "0x41", "0x4F", "0x50", "0x54", "0x20"
            ]
            assert all(
                call[1]["entity_category"] == EntityCategory.DIAGNOSTIC
                for call in calls[14:19]
            )

    def test_get_binary_sensor_entities_count(self, washing_machine_device, mock_coordinator):
        """Test that get_binary_sensor_entities returns correct number of sensors."""
        sensors = washing_machine_device.get_binary_sensor_entities(mock_coordinator)
        assert len(sensors) == 10  # 4 washer phases + detergent + softener + remote + 3 dryer phases

    def test_get_binary_sensor_entities_types(self, washing_machine_device, mock_coordinator):
        """Test that binary sensor entities have correct configurations."""
        with patch('custom_components.panasonic_iot_tw.binary_sensor.PanasonicBinarySensor') as mock_sensor_class:
            mock_sensor_instance = Mock()
            mock_sensor_class.return_value = mock_sensor_instance

            sensors = washing_machine_device.get_binary_sensor_entities(mock_coordinator)

            # Should create 10 binary sensors
            assert mock_sensor_class.call_count == 10

            calls = mock_sensor_class.call_args_list

            # Check engineering info sensors (first 4)
            engineering_info_calls = calls[0:4]
            for call in engineering_info_calls:
                args, kwargs = call
                assert kwargs['command_type'] == "0x34"  # Engineering info command
                assert kwargs['device_class'] == BinarySensorDeviceClass.RUNNING

            # The phases are decoded from the pending-phase word instead of
            # testing their own bit, so only one is ever active.
            for call, phase in zip(
                engineering_info_calls, ("prewash", "washing", "rinsing", "spinning")
            ):
                assert 'bit_mask' not in call[1]
                assert call[1]['gate_command'] == "0x50"
                assert call[1]['value_processor'](16) is (phase == "spinning")
                assert call[1]['value_processor'](112) is (phase == "washing")

            # Detergent flag sits between the stage bits and remote control
            args, kwargs = calls[4]
            assert kwargs['command_type'] == "0x71"
            assert kwargs['sensor_key'] == "detergent_low"
            assert kwargs['bit_mask'] == 0x0100

            # Softener flag sits next to the detergent flag on 0x71
            args, kwargs = calls[5]
            assert kwargs['command_type'] == "0x71"
            assert kwargs['sensor_key'] == "softener_low"
            assert kwargs['bit_mask'] == 0x0001

            # Check remote control sensor (last one)
            args, kwargs = calls[6]
            assert kwargs['command_type'] == "0x74"
            assert kwargs['sensor_key'] == "remote_control_allowed"
            # Panel permission is not connectivity; it is a diagnostic flag
            assert 'device_class' not in kwargs
            assert kwargs['entity_category'] == EntityCategory.DIAGNOSTIC

            # The appended combo-dryer phase sensors decode the low bits of 0x34.
            for call, phase, value in zip(
                calls[7:10],
                ("drying", "air_flow", "soft_cooling"),
                (8, 4, 2),
            ):
                assert call[1]["command_type"] == "0x34"
                assert call[1]["gate_command"] == "0x50"
                assert call[1]["sensor_key"] == {
                    "drying": "drying_status",
                    "air_flow": "air_flow_status",
                    "soft_cooling": "soft_cooling_status",
                }[phase]
                assert call[1]["value_processor"](value) is True

    def test_engineering_info_sensor_keys(self, washing_machine_device, mock_coordinator):
        """Test that engineering info binary sensors have correct sensor keys."""
        with patch('custom_components.panasonic_iot_tw.binary_sensor.PanasonicBinarySensor') as mock_sensor_class:
            mock_sensor_instance = Mock()
            mock_sensor_class.return_value = mock_sensor_instance

            sensors = washing_machine_device.get_binary_sensor_entities(mock_coordinator)

            calls = mock_sensor_class.call_args_list

            # Check sensor keys for engineering info
            assert calls[0][1]['sensor_key'] == "prewash_status"
            assert calls[1][1]['sensor_key'] == "washing_status"
            assert calls[2][1]['sensor_key'] == "rinsing_status"
            assert calls[3][1]['sensor_key'] == "spinning_status"

    def test_empty_entity_methods(self, washing_machine_device, mock_coordinator):
        """Test that unsupported entity types return empty lists."""
        assert washing_machine_device.get_switch_entities(mock_coordinator) == []
        assert washing_machine_device.get_select_entities(mock_coordinator) == []
        assert washing_machine_device.get_number_entities(mock_coordinator) == []
        assert washing_machine_device.get_climate_entities(mock_coordinator) == []
        assert washing_machine_device.get_humidifier_entities(mock_coordinator) == []
        assert washing_machine_device.get_button_entities(mock_coordinator) == []

    def test_sensor_inheritance_pattern(self, washing_machine_device):
        """Test that washing machine properly inherits from base device."""
        # This tests the inherited methods from StatusReader and BaseDevice
        assert hasattr(washing_machine_device, 'get_int_status')
        assert hasattr(washing_machine_device, 'get_boolean_status')
        assert hasattr(washing_machine_device, 'get_status_value')
        assert hasattr(washing_machine_device, 'device_data')

    def test_device_data_property(self, washing_machine_device, mock_device_data):
        """Test that device_data property returns correct data."""
        device_data = washing_machine_device.device_data

        # Should preserve all original fields
        assert device_data["device_id"] == mock_device_data["device_id"]
        assert device_data["nickname"] == mock_device_data["nickname"]
        assert device_data["model"] == mock_device_data["model"]
        assert device_data["version"] == mock_device_data["version"]
        assert device_data["device_type"] == mock_device_data["device_type"]
        assert device_data["available"] == mock_device_data["available"]

    def test_remote_control_sensor_configuration(self, washing_machine_device, mock_coordinator):
        """Test that remote control sensor has special value processor."""
        with patch('custom_components.panasonic_iot_tw.binary_sensor.PanasonicBinarySensor') as mock_sensor_class:
            mock_sensor_instance = Mock()
            mock_sensor_class.return_value = mock_sensor_instance

            sensors = washing_machine_device.get_binary_sensor_entities(mock_coordinator)

            # Check remote control sensor (last call)
            remote_control_call = mock_sensor_class.call_args_list[-1]
            args, kwargs = remote_control_call

            # Should have value_processor for remote control
            assert 'value_processor' in kwargs

    def test_translation_keys(self, washing_machine_device, mock_coordinator):
        """Test that all sensors have proper translation keys."""
        with patch('custom_components.panasonic_iot_tw.sensor.PanasonicSensor') as mock_sensor_class, \
             patch('custom_components.panasonic_iot_tw.binary_sensor.PanasonicBinarySensor') as mock_binary_sensor_class:

            mock_sensor_instance = Mock()
            mock_sensor_class.return_value = mock_sensor_instance
            mock_binary_sensor_class.return_value = mock_sensor_instance

            # Test sensor translation keys
            sensors = washing_machine_device.get_sensor_entities(mock_coordinator)
            sensor_calls = mock_sensor_class.call_args_list

            assert sensor_calls[0][1]['translation_key'] == "washing_machine_washing_remaining_time"
            assert sensor_calls[1][1]['translation_key'] == "washing_machine_schedule_remaining_time"
            assert sensor_calls[2][1]['translation_key'] == "washing_machine_operation_status"
            assert sensor_calls[3][1]['translation_key'] == "washing_machine_cycle_message"
            assert sensor_calls[4][1]['translation_key'] == "washing_machine_current_stage"
            assert sensor_calls[11][1]['translation_key'] == "washing_machine_drying_remaining_time"
            assert sensor_calls[12][1]['translation_key'] == "washing_machine_drying_error_code"

            # Reset mock
            mock_binary_sensor_class.reset_mock()

            # Test binary sensor translation keys
            binary_sensors = washing_machine_device.get_binary_sensor_entities(mock_coordinator)
            binary_calls = mock_binary_sensor_class.call_args_list

            expected_binary_keys = [
                "washing_machine_prewash_status",
                "washing_machine_washing_status",
                "washing_machine_rinsing_status",
                "washing_machine_spinning_status",
                "washing_machine_detergent_low",
                "washing_machine_softener_low",
                "washing_machine_remote_control_allowed",
                "washing_machine_drying_status",
                "washing_machine_air_flow_status",
                "washing_machine_soft_cooling_status"
            ]

            for i, expected_key in enumerate(expected_binary_keys):
                assert binary_calls[i][1]['translation_key'] == expected_key

    def test_device_info_inheritance(self, washing_machine_device):
        """Test that device info is properly inherited from BaseDevice."""
        device_info = washing_machine_device.get_device_info()

        assert device_info["identifiers"] == {("panasonic_iot_tw", "washing_machine_test_001")}
        assert device_info["name"] == "測試洗衣機"
        assert device_info["manufacturer"] == "Panasonic"
        assert device_info["model"] == "Test Washing Machine Model"