from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
)
from obi_energy_tracker import ConnectionStrength

from custom_components.obi_energy_tracker.const import DOMAIN
from custom_components.obi_energy_tracker.coordinator import (
    ObiEnergyTrackerCoordinator,
    ObiEnergyTrackerData,
)
from custom_components.obi_energy_tracker.sensor import (
    DEVICE_SENSORS,
    ObiDeviceSensor,
    async_setup_entry,
)

from .conftest import (
    make_bridge,
    make_coordinator_data,
    make_device_data,
    make_outlet,
    make_sensor,
)

SENSOR_ID = "sensor-1"
OUTLET_ID = "outlet-1"
BRIDGE_ID = "bridge-1"


def _make_coordinator_mock(data: ObiEnergyTrackerData | None = None) -> MagicMock:
    mock = MagicMock(spec=ObiEnergyTrackerCoordinator)
    mock.async_add_listener = MagicMock(return_value=lambda: None)
    mock.last_update_success = True
    mock.data = data
    return mock


def _device_description(key: str):
    for description in DEVICE_SENSORS:
        if description.key == key:
            return description
    raise ValueError(f"No device description with key {key!r}")


def _make_data(**kwargs) -> ObiEnergyTrackerData:
    device = make_sensor(id=SENSOR_ID, bridge_id=BRIDGE_ID, **kwargs.pop("sensor", {}))
    bridge = make_bridge(id=BRIDGE_ID, sensors=[device])
    device_data = make_device_data(device=device, bridge=bridge, **kwargs)
    return make_coordinator_data(devices=[device_data], bridges=[bridge])


def _create_device_sensor(coord: MagicMock, key: str, device_id: str = SENSOR_ID):
    return ObiDeviceSensor(coord, device_id, _device_description(key))


def _setup_entities(coord: MagicMock) -> list:
    hass_mock = MagicMock()
    entry_mock = MagicMock()
    entry_mock.entry_id = "test-entry-id"
    entry_mock.runtime_data = coord
    added: list = []
    return hass_mock, entry_mock, added


class TestEntityMetadata:
    def test_unique_id(self):
        coord = _make_coordinator_mock(_make_data())
        entity = _create_device_sensor(coord, "consumption")

        assert entity.unique_id == f"{SENSOR_ID}_consumption"

    def test_device_identifiers(self):
        coord = _make_coordinator_mock(_make_data())
        entity = _create_device_sensor(coord, "battery_level")

        assert entity.device_info["identifiers"] == {(DOMAIN, SENSOR_ID)}

    def test_via_device(self):
        coord = _make_coordinator_mock(_make_data())
        entity = _create_device_sensor(coord, "connection_strength")

        assert entity.device_info["via_device"] == (DOMAIN, BRIDGE_ID)

    def test_device_name_and_model(self):
        coord = _make_coordinator_mock(
            _make_data(sensor={"display_name": "Garage Meter"})
        )
        entity = _create_device_sensor(coord, "consumption")

        assert entity.device_info["name"] == "Garage Meter"
        assert entity.device_info["model"] == "ENERGY TRACKER Sensor"

    def test_outlet_model(self):
        outlet = make_outlet(id=OUTLET_ID, bridge_id=BRIDGE_ID)
        bridge = make_bridge(id=BRIDGE_ID, outlets=[outlet])
        data = make_coordinator_data(
            devices=[make_device_data(device=outlet, bridge=bridge)], bridges=[bridge]
        )
        coord = _make_coordinator_mock(data)
        entity = _create_device_sensor(coord, "consumption", device_id=OUTLET_ID)

        assert entity.device_info["model"] == "ENERGY TRACKER Outlet"

    def test_sw_hw_version(self):
        coord = _make_coordinator_mock(
            _make_data(sensor={"firmware_version": "4.5.6", "hardware_version": "7.8"})
        )
        entity = _create_device_sensor(coord, "consumption")

        assert entity.device_info["sw_version"] == "4.5.6"
        assert entity.device_info["hw_version"] == "7.8"

    def test_has_entity_name(self):
        coord = _make_coordinator_mock(_make_data())
        entity = _create_device_sensor(coord, "consumption")

        assert entity._attr_has_entity_name is True


class TestEntityDescriptions:
    def test_consumption_description(self):
        desc = _device_description("consumption")

        assert desc.native_unit_of_measurement == UnitOfEnergy.WATT_HOUR
        assert desc.device_class == SensorDeviceClass.ENERGY
        assert desc.state_class is None

    def test_feed_in_description(self):
        desc = _device_description("feed_in")

        assert desc.native_unit_of_measurement == UnitOfEnergy.WATT_HOUR
        assert desc.device_class == SensorDeviceClass.ENERGY
        assert desc.state_class is None
        assert desc.icon is None

    def test_battery_level_description(self):
        desc = _device_description("battery_level")

        assert desc.native_unit_of_measurement == PERCENTAGE
        assert desc.device_class == SensorDeviceClass.BATTERY
        assert desc.state_class == SensorStateClass.MEASUREMENT
        assert desc.entity_category == EntityCategory.DIAGNOSTIC

    def test_signal_strength_description(self):
        desc = _device_description("signal_strength")

        assert desc.native_unit_of_measurement == SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        assert desc.device_class == SensorDeviceClass.SIGNAL_STRENGTH
        assert desc.entity_category == EntityCategory.DIAGNOSTIC
        assert desc.entity_registry_enabled_default is False

    def test_connection_strength_description(self):
        desc = _device_description("connection_strength")

        assert desc.icon is None
        assert desc.native_unit_of_measurement is None
        assert desc.entity_category == EntityCategory.DIAGNOSTIC
        assert desc.device_class == SensorDeviceClass.ENUM
        assert desc.options == ["bad", "fair", "good", "excellent"]

    def test_description_keys(self):
        assert {d.key for d in DEVICE_SENSORS} == {
            "consumption",
            "feed_in",
            "battery_level",
            "signal_strength",
            "connection_strength",
        }


class TestNativeValue:
    def test_consumption(self):
        coord = _make_coordinator_mock(_make_data(energy=1234.5))
        assert _create_device_sensor(coord, "consumption").native_value == 1234.5

    def test_consumption_zero(self):
        coord = _make_coordinator_mock(_make_data(energy=0.0))
        assert _create_device_sensor(coord, "consumption").native_value == 0.0

    def test_consumption_none(self):
        coord = _make_coordinator_mock(_make_data(energy=None))
        assert _create_device_sensor(coord, "consumption").native_value is None

    def test_feed_in(self):
        coord = _make_coordinator_mock(_make_data(negative_energy=500.0))
        entity = _create_device_sensor(coord, "feed_in")
        assert entity.native_value == 500.0

    def test_feed_in_without_data_reads_zero(self):
        coord = _make_coordinator_mock(_make_data(negative_energy=0.0))
        assert _create_device_sensor(coord, "feed_in").native_value == 0.0

    def test_battery_level_from_metadata(self):
        coord = _make_coordinator_mock(_make_data(sensor={"battery_level": 85}))
        assert _create_device_sensor(coord, "battery_level").native_value == 85

    def test_battery_level_from_measure(self):
        coord = _make_coordinator_mock(
            _make_data(sensor={"battery_level": None}, battery=64.0)
        )
        assert _create_device_sensor(coord, "battery_level").native_value == 64

    def test_battery_level_none(self):
        coord = _make_coordinator_mock(
            _make_data(sensor={"battery_level": None}, battery=None)
        )
        assert _create_device_sensor(coord, "battery_level").native_value is None

    def test_signal_strength(self):
        coord = _make_coordinator_mock(_make_data(rssi=-93.0))
        assert _create_device_sensor(coord, "signal_strength").native_value == -93.0

    @pytest.mark.parametrize(
        ("rssi", "expected"),
        [
            (-99.0, ConnectionStrength.BAD),
            (-85.0, ConnectionStrength.FAIR),
            (-60.0, ConnectionStrength.GOOD),
            (-20.0, ConnectionStrength.EXCELLENT),
        ],
    )
    def test_connection_strength_states(
        self, rssi: float, expected: ConnectionStrength
    ):
        coord = _make_coordinator_mock(_make_data(rssi=rssi))
        entity = _create_device_sensor(coord, "connection_strength")

        assert entity.native_value == expected.value
        assert expected.value in _device_description("connection_strength").options

    def test_missing_connection_strength_creates_no_entity(self):
        data = _make_data(rssi=None)
        description = _device_description("connection_strength")

        assert description.exists_fn(data.devices[SENSOR_ID]) is False

    def test_value_none_when_device_gone(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        entity = _create_device_sensor(coord, "consumption")

        coord.data = ObiEnergyTrackerData(bridges=data.bridges, devices={})

        assert entity.native_value is None


class TestAvailability:
    def test_available(self):
        coord = _make_coordinator_mock(_make_data())
        assert _create_device_sensor(coord, "consumption").available is True

    def test_unavailable_when_device_missing(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        entity = _create_device_sensor(coord, "consumption")

        coord.data = ObiEnergyTrackerData(bridges=data.bridges, devices={})

        assert entity.available is False

    def test_unavailable_when_update_failed(self):
        coord = _make_coordinator_mock(_make_data())
        entity = _create_device_sensor(coord, "consumption")

        coord.last_update_success = False

        assert entity.available is False


class TestPlatformSetup:
    async def test_entities_for_sensor(self):
        coord = _make_coordinator_mock(_make_data(negative_energy=10.0, rssi=-70.0))
        hass_mock, entry_mock, _ = _setup_entities(coord)
        add_entities = MagicMock()

        await async_setup_entry(hass_mock, entry_mock, add_entities)

        entities = add_entities.call_args[0][0]
        keys = {e.entity_description.key for e in entities}
        assert keys == {
            "consumption",
            "feed_in",
            "battery_level",
            "signal_strength",
            "connection_strength",
        }

    async def test_entities_for_outlet(self):
        outlet = make_outlet(id=OUTLET_ID, bridge_id=BRIDGE_ID)
        bridge = make_bridge(id=BRIDGE_ID, outlets=[outlet])
        data = make_coordinator_data(
            devices=[
                make_device_data(
                    device=outlet,
                    bridge=bridge,
                    rssi=-35.0,
                    battery=None,
                )
            ],
            bridges=[bridge],
        )
        coord = _make_coordinator_mock(data)
        hass_mock, entry_mock, _ = _setup_entities(coord)
        add_entities = MagicMock()

        await async_setup_entry(hass_mock, entry_mock, add_entities)

        entities = add_entities.call_args[0][0]
        keys = {e.entity_description.key for e in entities}
        assert keys == {
            "consumption",
            "feed_in",
            "signal_strength",
            "connection_strength",
        }

    async def test_absent_measures_produce_no_entities(self):
        outlet = make_outlet(id=OUTLET_ID, bridge_id=BRIDGE_ID)
        bridge = make_bridge(id=BRIDGE_ID, outlets=[outlet])
        data = make_coordinator_data(
            devices=[
                make_device_data(
                    device=outlet,
                    bridge=bridge,
                    rssi=None,
                    battery=None,
                )
            ],
            bridges=[bridge],
        )
        coord = _make_coordinator_mock(data)
        hass_mock, entry_mock, _ = _setup_entities(coord)
        add_entities = MagicMock()

        await async_setup_entry(hass_mock, entry_mock, add_entities)

        entities = add_entities.call_args[0][0]
        keys = {e.entity_description.key for e in entities}
        assert keys == {
            "consumption",
            "feed_in",
        }

    async def test_multiple_devices(self):
        sensor_a = make_sensor(id="s-a", bridge_id=BRIDGE_ID)
        sensor_b = make_sensor(id="s-b", bridge_id=BRIDGE_ID)
        outlet = make_outlet(id="o-a", bridge_id=BRIDGE_ID)
        bridge = make_bridge(
            id=BRIDGE_ID, sensors=[sensor_a, sensor_b], outlets=[outlet]
        )
        data = make_coordinator_data(
            devices=[
                make_device_data(device=sensor_a, bridge=bridge),
                make_device_data(device=sensor_b, bridge=bridge),
                make_device_data(device=outlet, bridge=bridge),
            ],
            bridges=[bridge],
        )
        coord = _make_coordinator_mock(data)
        hass_mock, entry_mock, _ = _setup_entities(coord)
        add_entities = MagicMock()

        await async_setup_entry(hass_mock, entry_mock, add_entities)

        entities = add_entities.call_args[0][0]
        unique_ids = {e.unique_id for e in entities}
        assert "s-a_consumption" in unique_ids
        assert "s-b_consumption" in unique_ids
        assert "o-a_consumption" in unique_ids

    async def test_no_devices_no_entities(self):
        empty = ObiEnergyTrackerData()
        coord = _make_coordinator_mock(empty)
        hass_mock, entry_mock, _ = _setup_entities(coord)
        add_entities = MagicMock()

        await async_setup_entry(hass_mock, entry_mock, add_entities)

        add_entities.assert_not_called()

    async def test_listener_registered_for_dynamic_discovery(self):
        coord = _make_coordinator_mock(_make_data())
        hass_mock, entry_mock, _ = _setup_entities(coord)

        await async_setup_entry(hass_mock, entry_mock, MagicMock())

        coord.async_add_listener.assert_called_once()
        entry_mock.async_on_unload.assert_called_once()

    async def test_new_device_added_on_update(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        hass_mock, entry_mock, _ = _setup_entities(coord)
        add_entities = MagicMock()

        await async_setup_entry(hass_mock, entry_mock, add_entities)
        first_batch = {e.unique_id for e in add_entities.call_args[0][0]}
        listener = coord.async_add_listener.call_args[0][0]

        new_sensor = make_sensor(id="s-new", bridge_id=BRIDGE_ID)
        bridge = data.bridges[0]
        coord.data = make_coordinator_data(
            devices=[
                *data.devices.values(),
                make_device_data(device=new_sensor, bridge=bridge),
            ],
            bridges=[bridge],
        )

        listener()

        second_batch = {e.unique_id for e in add_entities.call_args[0][0]}
        assert "s-new_consumption" in second_batch

        assert not (second_batch & first_batch)
