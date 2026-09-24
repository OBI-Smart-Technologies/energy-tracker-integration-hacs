from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.obi_energy_tracker.binary_sensor import (
    ONLINE_DESCRIPTION,
    ObiOnlineBinarySensor,
    async_setup_entry,
)
from custom_components.obi_energy_tracker.const import DOMAIN
from custom_components.obi_energy_tracker.coordinator import (
    ObiEnergyTrackerCoordinator,
    ObiEnergyTrackerData,
)

from .conftest import (
    make_bridge,
    make_coordinator_data,
    make_device_data,
    make_outlet,
    make_sensor,
)

SENSOR_ID = "sensor-1"
BRIDGE_ID = "bridge-1"


def _make_data(is_online: bool = True, with_outlet: bool = False):
    sensor = make_sensor(id=SENSOR_ID, bridge_id=BRIDGE_ID, is_online=is_online)
    outlets = [make_outlet(id="o-1", bridge_id=BRIDGE_ID)] if with_outlet else []
    bridge = make_bridge(id=BRIDGE_ID, sensors=[sensor], outlets=outlets)
    devices = [make_device_data(device=sensor, bridge=bridge)]
    devices += [make_device_data(device=o, bridge=bridge) for o in outlets]
    return make_coordinator_data(devices=devices, bridges=[bridge])


def _make_coordinator_mock(data: ObiEnergyTrackerData | None = None) -> MagicMock:
    mock = MagicMock(spec=ObiEnergyTrackerCoordinator)
    mock.async_add_listener = MagicMock(return_value=lambda: None)
    mock.last_update_success = True
    mock.data = data
    return mock


class TestDescription:
    def test_connectivity_device_class(self):
        assert ONLINE_DESCRIPTION.device_class == BinarySensorDeviceClass.CONNECTIVITY

    def test_diagnostic_category(self):
        assert ONLINE_DESCRIPTION.entity_category == EntityCategory.DIAGNOSTIC


class TestEntity:
    def test_unique_id(self):
        coord = _make_coordinator_mock(_make_data())
        entity = ObiOnlineBinarySensor(coord, SENSOR_ID, ONLINE_DESCRIPTION)

        assert entity.unique_id == f"{SENSOR_ID}_is_online"

    def test_device_info(self):
        coord = _make_coordinator_mock(_make_data())
        entity = ObiOnlineBinarySensor(coord, SENSOR_ID, ONLINE_DESCRIPTION)

        assert entity.device_info == {"identifiers": {(DOMAIN, SENSOR_ID)}}

    def test_is_on_when_online(self):
        coord = _make_coordinator_mock(_make_data(is_online=True))
        entity = ObiOnlineBinarySensor(coord, SENSOR_ID, ONLINE_DESCRIPTION)

        assert entity.is_on is True

    def test_is_off_when_offline(self):
        coord = _make_coordinator_mock(_make_data(is_online=False))
        entity = ObiOnlineBinarySensor(coord, SENSOR_ID, ONLINE_DESCRIPTION)

        assert entity.is_on is False

    def test_none_when_device_gone(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        entity = ObiOnlineBinarySensor(coord, SENSOR_ID, ONLINE_DESCRIPTION)

        coord.data = ObiEnergyTrackerData(bridges=data.bridges, devices={})

        assert entity.is_on is None
        assert entity.available is False

    def test_available_even_when_device_offline(self):
        coord = _make_coordinator_mock(_make_data(is_online=False))
        entity = ObiOnlineBinarySensor(coord, SENSOR_ID, ONLINE_DESCRIPTION)

        assert entity.available is True


class TestPlatformSetup:
    async def _setup(self, coord: MagicMock) -> MagicMock:
        hass_mock = MagicMock()
        entry_mock = MagicMock()
        entry_mock.entry_id = "entry-1"
        entry_mock.runtime_data = coord
        add_entities = MagicMock()
        await async_setup_entry(hass_mock, entry_mock, add_entities)
        return add_entities

    async def test_entity_for_sensor_and_outlet(self):
        coord = _make_coordinator_mock(_make_data(with_outlet=True))

        add_entities = await self._setup(coord)

        entities = add_entities.call_args[0][0]
        assert {e.unique_id for e in entities} == {
            f"{SENSOR_ID}_is_online",
            "o-1_is_online",
        }

    async def test_no_devices_no_entities(self):
        coord = _make_coordinator_mock(ObiEnergyTrackerData())

        add_entities = await self._setup(coord)

        add_entities.assert_not_called()

    async def test_new_device_added_dynamically(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        add_entities = await self._setup(coord)
        listener = coord.async_add_listener.call_args[0][0]

        bridge = data.bridges[0]
        new_sensor = make_sensor(id="s-new", bridge_id=BRIDGE_ID)
        coord.data = make_coordinator_data(
            devices=[
                *data.devices.values(),
                make_device_data(device=new_sensor, bridge=bridge),
            ],
            bridges=[bridge],
        )

        listener()

        entities = add_entities.call_args[0][0]
        assert [e.unique_id for e in entities] == ["s-new_is_online"]
