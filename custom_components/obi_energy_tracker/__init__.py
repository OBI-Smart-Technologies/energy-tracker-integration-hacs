from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from obi_energy_tracker import ObiEnergyTrackerApi, ObiEnergyTrackerError

from .const import (
    DOMAIN,
    MANUFACTURER,
    MODEL_BRIDGE,
    MODEL_OUTLET,
    MODEL_SENSOR,
)
from .coordinator import ObiEnergyTrackerConfigEntry, ObiEnergyTrackerCoordinator
from .oauth2 import KeycloakOAuth2Implementation, oauth_token_provider

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ObiEnergyTrackerConfigEntry
) -> bool:
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, KeycloakOAuth2Implementation(hass)
    )

    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    api = ObiEnergyTrackerApi(
        session=async_get_clientsession(hass),
        token_provider=oauth_token_provider(oauth_session),
    )

    coordinator = ObiEnergyTrackerCoordinator(hass, entry, api)
    try:
        await coordinator.async_setup()
    except ObiEnergyTrackerError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    _async_register_devices(hass, entry, coordinator)

    entry.async_create_background_task(
        hass, _async_import_history(coordinator), f"{DOMAIN}_import_history"
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: ObiEnergyTrackerConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    data = entry.runtime_data.data
    known = {bridge.id for bridge in data.bridges} | set(data.devices)
    return not any(
        identifier[1] in known
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN
    )


async def _async_import_history(coordinator: ObiEnergyTrackerCoordinator) -> None:
    try:
        await coordinator.async_import_historical_statistics()
    except Exception:
        _LOGGER.exception("Failed to import historical statistics")


def _async_register_devices(
    hass: HomeAssistant,
    entry: ObiEnergyTrackerConfigEntry,
    coordinator: ObiEnergyTrackerCoordinator,
) -> None:
    device_reg = dr.async_get(hass)
    for bridge in coordinator.data.bridges:
        device_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, bridge.id)},
            name=bridge.display_name,
            manufacturer=MANUFACTURER,
            model=MODEL_BRIDGE,
            sw_version=bridge.firmware_version,
            hw_version=bridge.hardware_version,
        )
        for device in bridge.devices:
            device_reg.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, device.id)},
                name=device.display_name,
                manufacturer=MANUFACTURER,
                model=MODEL_OUTLET if device.is_outlet else MODEL_SENSOR,
                sw_version=device.firmware_version,
                hw_version=device.hardware_version,
                via_device=(DOMAIN, bridge.id),
            )


async def async_unload_entry(
    hass: HomeAssistant, entry: ObiEnergyTrackerConfigEntry
) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
