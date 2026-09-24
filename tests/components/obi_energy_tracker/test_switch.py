from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.exceptions import HomeAssistantError
from obi_energy_tracker import (
    ObiEnergyTrackerDeviceOfflineError,
    ObiEnergyTrackerError,
    OutletState,
)

from custom_components.obi_energy_tracker.const import DOMAIN
from custom_components.obi_energy_tracker.coordinator import (
    ObiEnergyTrackerCoordinator,
    ObiEnergyTrackerData,
)
from custom_components.obi_energy_tracker.switch import (
    OUTLET_DESCRIPTION,
    ObiOutletSwitch,
    async_setup_entry,
)

from .conftest import (
    make_bridge,
    make_coordinator_data,
    make_device_data,
    make_outlet,
    make_sensor,
)

OUTLET_ID = "outlet-1"
BRIDGE_ID = "bridge-1"


def _make_data(
    state: OutletState | None = OutletState.OFF,
    is_online: bool = True,
    with_sensor=False,
):
    outlet = make_outlet(
        id=OUTLET_ID, bridge_id=BRIDGE_ID, state=state, is_online=is_online
    )
    sensors = [make_sensor(id="s-1", bridge_id=BRIDGE_ID)] if with_sensor else []
    bridge = make_bridge(id=BRIDGE_ID, sensors=sensors, outlets=[outlet])
    devices = [make_device_data(device=outlet, bridge=bridge)]
    devices += [make_device_data(device=s, bridge=bridge) for s in sensors]
    return make_coordinator_data(devices=devices, bridges=[bridge])


def _make_coordinator_mock(data: ObiEnergyTrackerData | None = None) -> MagicMock:
    mock = MagicMock(spec=ObiEnergyTrackerCoordinator)
    mock.async_add_listener = MagicMock(return_value=lambda: None)
    mock.last_update_success = True
    mock.data = data
    mock.async_set_outlet_state = AsyncMock()
    return mock


def _make_switch(coord: MagicMock) -> ObiOutletSwitch:
    entity = ObiOutletSwitch(coord, OUTLET_ID, OUTLET_DESCRIPTION)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestDescription:
    def test_device_class_outlet(self):
        assert OUTLET_DESCRIPTION.device_class == SwitchDeviceClass.OUTLET

    def test_translation_key(self):
        assert OUTLET_DESCRIPTION.translation_key == "outlet"


class TestEntityMetadata:
    def test_unique_id(self):
        coord = _make_coordinator_mock(_make_data())
        assert _make_switch(coord).unique_id == f"{OUTLET_ID}_outlet"

    def test_device_info(self):
        coord = _make_coordinator_mock(_make_data())
        entity = _make_switch(coord)

        assert entity.device_info["identifiers"] == {(DOMAIN, OUTLET_ID)}
        assert entity.device_info["via_device"] == (DOMAIN, BRIDGE_ID)
        assert entity.device_info["model"] == "ENERGY TRACKER Outlet"


class TestState:
    def test_on(self):
        coord = _make_coordinator_mock(_make_data(state=OutletState.ON))
        assert _make_switch(coord).is_on is True

    def test_off(self):
        coord = _make_coordinator_mock(_make_data(state=OutletState.OFF))
        assert _make_switch(coord).is_on is False

    def test_unknown_state(self):
        coord = _make_coordinator_mock(_make_data(state=None))
        assert _make_switch(coord).is_on is None

    def test_none_when_device_gone(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        entity = _make_switch(coord)

        coord.data = ObiEnergyTrackerData(bridges=data.bridges, devices={})

        assert entity.is_on is None


class TestAvailability:
    def test_available_when_online(self):
        coord = _make_coordinator_mock(_make_data(is_online=True))
        assert _make_switch(coord).available is True

    def test_unavailable_when_offline(self):
        coord = _make_coordinator_mock(_make_data(is_online=False))
        assert _make_switch(coord).available is False


class TestSwitching:
    async def test_turn_on_calls_coordinator(self):
        coord = _make_coordinator_mock(_make_data(state=OutletState.OFF))
        entity = _make_switch(coord)

        await entity.async_turn_on()

        coord.async_set_outlet_state.assert_awaited_once_with(OUTLET_ID, OutletState.ON)

    async def test_turn_off_calls_coordinator(self):
        coord = _make_coordinator_mock(_make_data(state=OutletState.ON))
        entity = _make_switch(coord)

        await entity.async_turn_off()

        coord.async_set_outlet_state.assert_awaited_once_with(
            OUTLET_ID, OutletState.OFF
        )

    async def test_optimistic_state_before_refresh(self):
        coord = _make_coordinator_mock(_make_data(state=OutletState.OFF))
        entity = _make_switch(coord)

        await entity.async_turn_on()

        assert coord.data.devices[OUTLET_ID].device.state is OutletState.OFF
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called()

    async def test_optimistic_state_cleared_on_update(self):
        coord = _make_coordinator_mock(_make_data(state=OutletState.OFF))
        entity = _make_switch(coord)
        await entity.async_turn_on()

        entity._handle_coordinator_update()

        assert entity._optimistic_state is None
        assert entity.is_on is False

    async def test_offline_error_raises_home_assistant_error(self):
        coord = _make_coordinator_mock(_make_data(state=OutletState.OFF))
        coord.async_set_outlet_state.side_effect = ObiEnergyTrackerDeviceOfflineError(
            "no ack"
        )
        entity = _make_switch(coord)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "outlet_offline"
        assert entity._optimistic_state is None
        assert entity.is_on is False

    async def test_api_error_raises_home_assistant_error(self):
        coord = _make_coordinator_mock(_make_data(state=OutletState.OFF))
        coord.async_set_outlet_state.side_effect = ObiEnergyTrackerError("boom")
        entity = _make_switch(coord)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()

        assert err.value.translation_key == "outlet_switch_failed"
        assert entity._optimistic_state is None


class TestPlatformSetup:
    async def _setup(self, coord: MagicMock) -> MagicMock:
        hass_mock = MagicMock()
        entry_mock = MagicMock()
        entry_mock.entry_id = "entry-1"
        entry_mock.runtime_data = coord
        add_entities = MagicMock()
        await async_setup_entry(hass_mock, entry_mock, add_entities)
        return add_entities

    async def test_switch_created_for_outlet_only(self):
        coord = _make_coordinator_mock(_make_data(with_sensor=True))

        add_entities = await self._setup(coord)

        entities = add_entities.call_args[0][0]
        assert [e.unique_id for e in entities] == [f"{OUTLET_ID}_outlet"]

    async def test_no_entities_without_outlets(self):
        sensor = make_sensor(id="s-1", bridge_id=BRIDGE_ID)
        bridge = make_bridge(id=BRIDGE_ID, sensors=[sensor])
        data = make_coordinator_data(
            devices=[make_device_data(device=sensor, bridge=bridge)], bridges=[bridge]
        )
        coord = _make_coordinator_mock(data)

        add_entities = await self._setup(coord)

        add_entities.assert_not_called()

    async def test_no_devices_no_entities(self):
        bridge = make_bridge(id=BRIDGE_ID)
        coord = _make_coordinator_mock(
            make_coordinator_data(devices=[], bridges=[bridge])
        )

        add_entities = await self._setup(coord)

        add_entities.assert_not_called()

    async def test_new_outlet_added_dynamically(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        add_entities = await self._setup(coord)
        listener = coord.async_add_listener.call_args[0][0]

        bridge = data.bridges[0]
        new_outlet = make_outlet(id="o-new", bridge_id=BRIDGE_ID)
        coord.data = make_coordinator_data(
            devices=[
                *data.devices.values(),
                make_device_data(device=new_outlet, bridge=bridge),
            ],
            bridges=[bridge],
        )

        listener()

        entities = add_entities.call_args[0][0]
        assert [e.unique_id for e in entities] == ["o-new_outlet"]
