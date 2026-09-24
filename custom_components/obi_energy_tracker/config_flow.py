from __future__ import annotations

import base64
import json
import logging
from typing import Any, override

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from obi_energy_tracker import (
    ObiEnergyTrackerApi,
    ObiEnergyTrackerError,
    static_token_provider,
)

from .const import DOMAIN, OAUTH2_SCOPES
from .oauth2 import KeycloakOAuth2Implementation, async_revoke_session

_LOGGER = logging.getLogger(__name__)


def _extract_account_id(access_token: str) -> str | None:
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError, TypeError:
        return None
    if not isinstance(claims, dict):
        return None
    account_id = claims.get("accountId")
    return account_id if isinstance(account_id, str) else None


class ObiEnergyTrackerConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    DOMAIN = DOMAIN
    VERSION = 1

    @staticmethod
    @callback
    @override
    def async_get_options_flow(entry: ConfigEntry) -> ObiEnergyTrackerOptionsFlow:
        return ObiEnergyTrackerOptionsFlow()

    @property
    @override
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    @override
    def extra_authorize_data(self) -> dict[str, Any]:
        return {"scope": OAUTH2_SCOPES}

    def _register_implementation(self) -> None:
        self.async_register_implementation(
            self.hass, KeycloakOAuth2Implementation(self.hass)
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._register_implementation()
        return await self.async_step_pick_implementation()

    async def _async_api_reachable(self, access_token: str) -> bool:
        api = ObiEnergyTrackerApi(
            session=async_get_clientsession(self.hass),
            token_provider=static_token_provider(access_token),
        )
        try:
            await api.async_get_bridges()
        except ObiEnergyTrackerError as err:
            _LOGGER.error("OBI ENERGY TRACKER API is not reachable: %s", err)
            return False
        return True

    @override
    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        access_token = data["token"].get("access_token", "")
        if not await self._async_api_reachable(access_token):
            return self.async_abort(reason="cannot_connect")

        account_id = _extract_account_id(access_token)
        if account_id:
            await self.async_set_unique_id(account_id)

        if self.source == SOURCE_REAUTH:
            reauth_entry = self._get_reauth_entry()
            if reauth_entry.unique_id is not None and account_id is None:
                return self.async_abort(reason="wrong_account")
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(reauth_entry, data=data)

        if account_id:
            self._abort_if_unique_id_configured()
        else:
            self._async_abort_entries_match()

        return self.async_create_entry(title="OBI ENERGY TRACKER", data=data)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._register_implementation()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")

        return await self.async_step_pick_implementation()


class ObiEnergyTrackerOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="init", data_schema=vol.Schema({}))

        await async_revoke_session(self.hass, self.config_entry)
        self.config_entry.async_start_reauth(self.hass)
        return self.async_abort(reason="logged_out")
