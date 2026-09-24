from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.icon import async_get_icons
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from obi_energy_tracker import ObiEnergyTrackerError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.obi_energy_tracker import (
    PLATFORMS,
    async_remove_config_entry_device,
    async_setup_entry,
    async_unload_entry,
    binary_sensor,
    sensor,
    switch,
    update,
)
from custom_components.obi_energy_tracker.const import DOMAIN

from .conftest import (
    make_bridge,
    make_coordinator_data,
    make_device_data,
    make_mock_oauth_implementation,
    make_mock_oauth_session,
    make_outlet,
    make_sensor,
)


def _patch_coordinator_and_api(bridges=None, data=None, hass=None):
    if data is None:
        if bridges is None:
            bridges = [make_bridge()]
        devices = [
            make_device_data(device=device, bridge=bridge)
            for bridge in bridges
            for device in bridge.devices
        ]
        data = make_coordinator_data(devices=devices, bridges=bridges)

    coord_patch = patch(
        "custom_components.obi_energy_tracker.ObiEnergyTrackerCoordinator"
    )
    api_patch = patch("custom_components.obi_energy_tracker.ObiEnergyTrackerApi")
    oauth_impl_patch = patch(
        "custom_components.obi_energy_tracker.config_entry_oauth2_flow."
        "async_get_config_entry_implementation"
    )
    oauth_session_patch = patch(
        "custom_components.obi_energy_tracker.config_entry_oauth2_flow.OAuth2Session"
    )

    class _Ctx:
        def __init__(self):
            self.mock_coord_cls = None
            self.mock_coord = None
            self.mock_api_cls = None
            self.mock_oauth_session = None
            self._coord_cm = coord_patch
            self._api_cm = api_patch
            self._oauth_impl_cm = oauth_impl_patch
            self._oauth_session_cm = oauth_session_patch
            self._hass = hass
            self._forward_cm = None
            self._unload_cm = None

        def __enter__(self):
            self.mock_coord_cls = self._coord_cm.__enter__()
            self.mock_api_cls = self._api_cm.__enter__()
            mock_get_impl = self._oauth_impl_cm.__enter__()
            mock_get_impl.return_value = make_mock_oauth_implementation()
            mock_session_cls = self._oauth_session_cm.__enter__()
            self.mock_oauth_session = make_mock_oauth_session()
            mock_session_cls.return_value = self.mock_oauth_session
            self.mock_coord = self.mock_coord_cls.return_value
            self.mock_coord.async_setup = AsyncMock()
            self.mock_coord.async_config_entry_first_refresh = AsyncMock()
            self.mock_coord.async_import_historical_statistics = AsyncMock()
            self.mock_coord.data = data
            if self._hass is not None:
                self._forward_cm = patch.object(
                    self._hass.config_entries,
                    "async_forward_entry_setups",
                    new=AsyncMock(),
                )
                self._forward_cm.__enter__()
                self._unload_cm = patch.object(
                    self._hass.config_entries,
                    "async_unload_platforms",
                    new=AsyncMock(return_value=True),
                )
                self._unload_cm.__enter__()
            return self

        def __exit__(self, *exc):
            if self._unload_cm is not None:
                self._unload_cm.__exit__(*exc)
            if self._forward_cm is not None:
                self._forward_cm.__exit__(*exc)
            self._oauth_session_cm.__exit__(*exc)
            self._oauth_impl_cm.__exit__(*exc)
            self._api_cm.__exit__(*exc)
            self._coord_cm.__exit__(*exc)

    return _Ctx()


class TestPlatforms:
    def test_platforms(self) -> None:
        assert set(PLATFORMS) == {
            Platform.BINARY_SENSOR,
            Platform.SENSOR,
            Platform.SWITCH,
            Platform.UPDATE,
        }


class TestAsyncSetupEntry:
    async def test_coordinator_stored_in_runtime_data(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass) as ctx:
            await async_setup_entry(hass, mock_config_entry)

        assert mock_config_entry.runtime_data is ctx.mock_coord

    async def test_coordinator_setup_called(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass) as ctx:
            await async_setup_entry(hass, mock_config_entry)

        ctx.mock_coord.async_setup.assert_called_once()

    async def test_coordinator_first_refresh_called(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass) as ctx:
            await async_setup_entry(hass, mock_config_entry)

        ctx.mock_coord.async_config_entry_first_refresh.assert_called_once()

    async def test_bridge_device_registered(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        bridge = make_bridge(
            id="bridge-99",
            label="My Bridge",
            firmware_version="3.2.1",
            hardware_version="4.0.0",
        )
        with _patch_coordinator_and_api(bridges=[bridge], hass=hass):
            await async_setup_entry(hass, mock_config_entry)

        device = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, "bridge-99")}
        )
        assert device is not None
        assert device.name == "OBI Bridge My Bridge"
        assert device.manufacturer == "OBI"
        assert device.model == "ENERGY TRACKER Bridge"
        assert device.sw_version == "3.2.1"
        assert device.hw_version == "4.0.0"

    async def test_sensor_and_outlet_devices_registered(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        bridge = make_bridge(
            id="br-1",
            sensors=[
                make_sensor(id="s-1", bridge_id="br-1", display_name="Wohnung"),
                make_sensor(id="s-2", bridge_id="br-1", display_name="Garage"),
            ],
            outlets=[make_outlet(id="o-1", bridge_id="br-1", display_name="Terrasse")],
        )
        with _patch_coordinator_and_api(bridges=[bridge], hass=hass):
            await async_setup_entry(hass, mock_config_entry)

        device_reg = dr.async_get(hass)
        sensor_device = device_reg.async_get_device(identifiers={(DOMAIN, "s-2")})
        outlet_device = device_reg.async_get_device(identifiers={(DOMAIN, "o-1")})
        bridge_device = device_reg.async_get_device(identifiers={(DOMAIN, "br-1")})

        assert sensor_device is not None
        assert sensor_device.name == "Garage"
        assert sensor_device.model == "ENERGY TRACKER Sensor"
        assert sensor_device.via_device_id == bridge_device.id
        assert outlet_device is not None
        assert outlet_device.name == "Terrasse"
        assert outlet_device.model == "ENERGY TRACKER Outlet"
        assert outlet_device.via_device_id == bridge_device.id

    async def test_platforms_forwarded(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass):
            await async_setup_entry(hass, mock_config_entry)
            hass.config_entries.async_forward_entry_setups.assert_called_once_with(
                mock_config_entry, PLATFORMS
            )

    async def test_statistics_import_scheduled(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass) as ctx:
            await async_setup_entry(hass, mock_config_entry)
            await hass.async_block_till_done()

        ctx.mock_coord.async_import_historical_statistics.assert_awaited_once()

    async def test_api_error_raises_config_entry_not_ready(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass) as ctx:
            ctx.mock_coord.async_setup.side_effect = ObiEnergyTrackerError("down")
            with pytest.raises(ConfigEntryNotReady) as err:
                await async_setup_entry(hass, mock_config_entry)

        assert err.value.translation_key == "cannot_connect"

    async def test_coordinator_setup_failure_propagates(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass) as ctx:
            ctx.mock_coord.async_setup.side_effect = RuntimeError("boom")
            with pytest.raises(RuntimeError, match="boom"):
                await async_setup_entry(hass, mock_config_entry)

    async def test_no_bridges_still_succeeds(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(bridges=[], hass=hass):
            result = await async_setup_entry(hass, mock_config_entry)

        assert result is True
        device_reg = dr.async_get(hass)
        assert (
            dr.async_entries_for_config_entry(device_reg, mock_config_entry.entry_id)
            == []
        )

    async def test_api_uses_the_library_host(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass) as ctx:
            await async_setup_entry(hass, mock_config_entry)

        _, kwargs = ctx.mock_api_cls.call_args
        assert "base_url" not in kwargs
        assert ctx.mock_coord_cls.call_args.args[1] is mock_config_entry
        assert ctx.mock_coord_cls.call_args.args[2] is ctx.mock_api_cls.return_value

    async def test_api_gets_the_shared_session(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        with _patch_coordinator_and_api(hass=hass) as ctx:
            await async_setup_entry(hass, mock_config_entry)

        assert ctx.mock_api_cls.call_args.kwargs["session"] is not None


class TestAsyncUnloadEntry:
    async def _setup_entry(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        with _patch_coordinator_and_api(hass=hass):
            await async_setup_entry(hass, entry)

    async def test_returns_true_on_success(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        await self._setup_entry(hass, mock_config_entry)

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ):
            result = await async_unload_entry(hass, mock_config_entry)

        assert result is True


class TestRemoveConfigEntryDevice:
    async def _entry(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        with _patch_coordinator_and_api(hass=hass):
            await async_setup_entry(hass, entry)

    async def test_known_device_is_kept(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        sensor = make_sensor(id="sensor-001")
        bridge = make_bridge(id="bridge-001", sensors=[sensor])
        with _patch_coordinator_and_api(bridges=[bridge], hass=hass):
            await async_setup_entry(hass, mock_config_entry)

        device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, sensor.id)})

        assert (
            await async_remove_config_entry_device(hass, mock_config_entry, device)
            is False
        )

    async def test_known_bridge_is_kept(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        bridge = make_bridge(id="bridge-001", sensors=[make_sensor(id="sensor-001")])
        with _patch_coordinator_and_api(bridges=[bridge], hass=hass):
            await async_setup_entry(hass, mock_config_entry)

        device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, bridge.id)})

        assert (
            await async_remove_config_entry_device(hass, mock_config_entry, device)
            is False
        )

    async def test_unknown_device_is_removable(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        await self._entry(hass, mock_config_entry)

        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "sensor-gone")},
        )

        assert (
            await async_remove_config_entry_device(hass, mock_config_entry, device)
            is True
        )


class TestParallelUpdates:
    def test_read_only_platforms_are_unlimited(self) -> None:
        assert sensor.PARALLEL_UPDATES == 0
        assert binary_sensor.PARALLEL_UPDATES == 0

    def test_switch_is_serialised(self) -> None:
        assert switch.PARALLEL_UPDATES == 1

    def test_update_is_serialised(self) -> None:
        assert update.PARALLEL_UPDATES == 1


class TestTranslations:
    async def test_exception_messages_resolve(self, hass: HomeAssistant) -> None:
        assert await async_setup_component(hass, DOMAIN, {})

        result = await async_get_translations(hass, "en", "exceptions", {DOMAIN})

        assert set(result) == {
            f"component.{DOMAIN}.exceptions.{key}.message"
            for key in (
                "cannot_connect",
                "auth_failed",
                "update_failed",
                "outlet_offline",
                "outlet_switch_failed",
                "firmware_update_unavailable",
                "firmware_update_failed",
            )
        }

    async def test_german_exception_messages_resolve(self, hass: HomeAssistant) -> None:
        assert await async_setup_component(hass, DOMAIN, {})

        result = await async_get_translations(hass, "de", "exceptions", {DOMAIN})

        assert (
            result[f"component.{DOMAIN}.exceptions.outlet_offline.message"]
            == "Zwischenstecker {name} hat nicht reagiert, "
            "er ist möglicherweise offline."
        )

    async def test_connection_strength_icon_comes_from_icons_json(
        self, hass: HomeAssistant
    ) -> None:
        assert await async_setup_component(hass, DOMAIN, {})

        icons = await async_get_icons(hass, "entity", [DOMAIN])

        assert icons[DOMAIN]["sensor"]["connection_strength"]["default"] == "mdi:wifi"
