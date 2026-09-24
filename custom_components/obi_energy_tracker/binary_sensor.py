from __future__ import annotations

from collections.abc import Iterator
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ObiEnergyTrackerConfigEntry, ObiEnergyTrackerCoordinator
from .entity import ObiDeviceEntity, async_add_entities_dynamically

PARALLEL_UPDATES = 0

ONLINE_DESCRIPTION = BinarySensorEntityDescription(
    key="is_online",
    translation_key="is_online",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


def _build_entities(
    coordinator: ObiEnergyTrackerCoordinator,
) -> Iterator[tuple[str, Entity]]:
    for device_id in coordinator.data.devices:
        yield (
            f"{device_id}_{ONLINE_DESCRIPTION.key}",
            ObiOnlineBinarySensor(coordinator, device_id, ONLINE_DESCRIPTION),
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


class ObiOnlineBinarySensor(ObiDeviceEntity, BinarySensorEntity):
    def __init__(
        self,
        coordinator: ObiEnergyTrackerCoordinator,
        device_id: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    @override
    def is_on(self) -> bool | None:
        data = self.device_data
        return None if data is None else data.device.is_online
