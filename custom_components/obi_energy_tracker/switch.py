from __future__ import annotations

from collections.abc import Iterator
from typing import Any, override

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from obi_energy_tracker import (
    ObiEnergyTrackerDeviceOfflineError,
    ObiEnergyTrackerError,
    OutletState,
)

from .const import DOMAIN
from .coordinator import ObiEnergyTrackerConfigEntry, ObiEnergyTrackerCoordinator
from .entity import ObiDeviceEntity, async_add_entities_dynamically

PARALLEL_UPDATES = 1

OUTLET_DESCRIPTION = SwitchEntityDescription(
    key="outlet",
    translation_key="outlet",
    device_class=SwitchDeviceClass.OUTLET,
)


def _build_entities(
    coordinator: ObiEnergyTrackerCoordinator,
) -> Iterator[tuple[str, Entity]]:
    for device_id, device_data in coordinator.data.devices.items():
        if device_data.device.is_outlet:
            yield (
                f"{device_id}_{OUTLET_DESCRIPTION.key}",
                ObiOutletSwitch(coordinator, device_id, OUTLET_DESCRIPTION),
            )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ObiEnergyTrackerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entry.async_on_unload(
        async_add_entities_dynamically(coordinator, async_add_entities, _build_entities)
    )


class ObiOutletSwitch(ObiDeviceEntity, SwitchEntity):
    def __init__(
        self,
        coordinator: ObiEnergyTrackerCoordinator,
        device_id: str,
        description: SwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self._optimistic_state: OutletState | None = None

    @property
    @override
    def is_on(self) -> bool | None:
        data = self.device_data
        if data is None:
            return None
        state = self._optimistic_state or data.device.state
        return None if state is None else state is OutletState.ON

    @property
    @override
    def available(self) -> bool:
        data = self.device_data
        return super().available and data is not None and data.device.is_online

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_state(OutletState.ON)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_state(OutletState.OFF)

    async def _async_set_state(self, state: OutletState) -> None:
        data = self.device_data
        label = data.device.display_name if data else self._device_id
        self._optimistic_state = state
        self.async_write_ha_state()
        try:
            await self.coordinator.async_set_outlet_state(self._device_id, state)
        except ObiEnergyTrackerDeviceOfflineError as err:
            self._optimistic_state = None
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="outlet_offline",
                translation_placeholders={"name": label},
            ) from err
        except ObiEnergyTrackerError as err:
            self._optimistic_state = None
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="outlet_switch_failed",
                translation_placeholders={"name": label, "error": str(err)},
            ) from err

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._optimistic_state = None
        super()._handle_coordinator_update()
