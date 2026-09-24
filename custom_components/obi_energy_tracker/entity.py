from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import override

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from obi_energy_tracker import Bridge

from .const import DOMAIN
from .coordinator import DeviceData, ObiEnergyTrackerCoordinator


class ObiDeviceEntity(CoordinatorEntity[ObiEnergyTrackerCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ObiEnergyTrackerCoordinator,
        device_id: str,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})

    @property
    def device_data(self) -> DeviceData | None:
        return self.coordinator.data.devices.get(self._device_id)

    @property
    @override
    def available(self) -> bool:
        return super().available and self.device_data is not None


class ObiBridgeEntity(CoordinatorEntity[ObiEnergyTrackerCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ObiEnergyTrackerCoordinator,
        bridge: Bridge,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._bridge_id = bridge.id
        self._attr_unique_id = f"{bridge.id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, bridge.id)})

    @property
    def bridge(self) -> Bridge | None:
        return self.coordinator.data.bridge(self._bridge_id)

    @property
    @override
    def available(self) -> bool:
        return super().available and self.bridge is not None


@callback
def async_add_entities_dynamically(
    coordinator: ObiEnergyTrackerCoordinator,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[ObiEnergyTrackerCoordinator], Iterable[tuple[str, Entity]]],
) -> Callable[[], None]:
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new_entities: list[Entity] = []
        for key, entity in build(coordinator):
            if key in known:
                continue
            known.add(key)
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    _add_new()
    return coordinator.async_add_listener(_add_new)
