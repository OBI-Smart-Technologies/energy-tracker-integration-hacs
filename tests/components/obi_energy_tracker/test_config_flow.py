from __future__ import annotations

import base64
import hashlib
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.config_entry_oauth2_flow import MY_AUTH_CALLBACK_PATH
from homeassistant.helpers.http import current_request
from obi_energy_tracker import DEFAULT_BASE_URL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.obi_energy_tracker.config_flow import _extract_account_id
from custom_components.obi_energy_tracker.const import (
    DOMAIN,
    OAUTH2_CLIENT_ID,
    OAUTH2_LOGOUT_URL,
    OAUTH2_SCOPES,
)
from custom_components.obi_energy_tracker.oauth2 import (
    KeycloakOAuth2Implementation,
    async_revoke_session,
)

from .conftest import make_bridge_api_response, make_oauth_config_data

FAKE_TOKEN = {
    "access_token": "fake-access-token",
    "refresh_token": "fake-refresh-token",
    "token_type": "Bearer",
    "expires_in": 900,
    "expires_at": time.time() + 900,
    "scope": "openid email profile offline_access",
}


def _jwt(account_id: str | None) -> str:
    payload: dict[str, str] = {"sub": "user-123", "email": "test@example.com"}
    if account_id is not None:
        payload["accountId"] = account_id

    def segment(raw: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'RS256'})}.{segment(payload)}.fake-signature"


def _mock_implementation() -> MagicMock:
    mock_impl = MagicMock()
    mock_impl.name = "OBI ENERGY TRACKER"
    mock_impl.domain = DOMAIN
    mock_impl.async_generate_authorize_url = AsyncMock(
        return_value="https://auth.test.com/authorize?code_challenge=abc"
    )
    mock_impl.async_resolve_external_data = AsyncMock(return_value={**FAKE_TOKEN})
    return mock_impl


def _mock_bridges(mock_responses, status: int = 200) -> None:
    url = f"{DEFAULT_BASE_URL}/bridges"
    if status == 200:
        mock_responses.get(url, json=[make_bridge_api_response()])
    else:
        mock_responses.get(url, status=status, text="nope")


class TestOAuth2Flow:
    async def test_full_flow_creates_entry(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        _mock_bridges(mock_responses)
        with patch(
            "custom_components.obi_energy_tracker.config_flow."
            "KeycloakOAuth2Implementation",
            return_value=_mock_implementation(),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )

            if (
                result["type"] is FlowResultType.FORM
                and result["step_id"] == "pick_implementation"
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={"implementation": DOMAIN}
                )

            assert result["type"] is FlowResultType.EXTERNAL_STEP

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={"code": "test-auth-code"}
            )

        if result["type"] is FlowResultType.EXTERNAL_STEP_DONE:
            result = await hass.config_entries.flow.async_configure(result["flow_id"])

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "OBI ENERGY TRACKER"
        assert result["data"]["token"]["access_token"] == "fake-access-token"

    async def test_extra_authorize_data_includes_scopes(
        self, hass: HomeAssistant
    ) -> None:
        from custom_components.obi_energy_tracker.config_flow import (
            ObiEnergyTrackerConfigFlow,
        )

        flow = ObiEnergyTrackerConfigFlow()
        assert flow.extra_authorize_data == {"scope": OAUTH2_SCOPES}

    async def _finish_prod_flow(self, hass: HomeAssistant, token: dict):
        mock_impl = _mock_implementation()
        mock_impl.async_resolve_external_data = AsyncMock(return_value=token)
        with patch(
            "custom_components.obi_energy_tracker.config_flow."
            "KeycloakOAuth2Implementation",
            return_value=mock_impl,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            if (
                result["type"] is FlowResultType.FORM
                and result["step_id"] == "pick_implementation"
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={"implementation": DOMAIN}
                )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={"code": "test-auth-code"}
            )
        if result["type"] is FlowResultType.EXTERNAL_STEP_DONE:
            result = await hass.config_entries.flow.async_configure(result["flow_id"])
        return result

    async def test_unreachable_api_aborts_the_flow(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        _mock_bridges(mock_responses, status=500)

        result = await self._finish_prod_flow(hass, {**FAKE_TOKEN})

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"
        assert hass.config_entries.async_entries(DOMAIN) == []

    async def test_same_account_cannot_be_set_up_twice(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        _mock_bridges(mock_responses)
        MockConfigEntry(
            domain=DOMAIN,
            version=1,
            unique_id="acc-9",
            data=make_oauth_config_data(),
        ).add_to_hass(hass)

        result = await self._finish_prod_flow(
            hass, {**FAKE_TOKEN, "access_token": _jwt("acc-9")}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        assert len(hass.config_entries.async_entries(DOMAIN)) == 1

    async def test_token_without_account_id_still_deduplicates(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        _mock_bridges(mock_responses)
        MockConfigEntry(
            domain=DOMAIN,
            version=1,
            data=make_oauth_config_data(),
        ).add_to_hass(hass)

        result = await self._finish_prod_flow(
            hass, {**FAKE_TOKEN, "access_token": _jwt(None)}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        assert len(hass.config_entries.async_entries(DOMAIN)) == 1


class TestExtractAccountId:
    def test_valid_jwt_with_account_id(self) -> None:
        assert _extract_account_id(_jwt("acc-456")) == "acc-456"

    def test_jwt_without_account_id(self) -> None:
        assert _extract_account_id(_jwt(None)) is None

    def test_invalid_token_returns_none(self) -> None:
        assert _extract_account_id("not-a-jwt") is None

    def test_empty_string_returns_none(self) -> None:
        assert _extract_account_id("") is None

    def test_undecodable_payload_returns_none(self) -> None:
        assert _extract_account_id("header.!!not-base64!!.signature") is None

    def test_non_object_payload_returns_none(self) -> None:
        payload = base64.urlsafe_b64encode(b"[1, 2]").decode().rstrip("=")
        assert _extract_account_id(f"header.{payload}.signature") is None

    def test_non_string_account_id_returns_none(self) -> None:
        payload = (
            base64.urlsafe_b64encode(json.dumps({"accountId": 42}).encode())
            .decode()
            .rstrip("=")
        )
        assert _extract_account_id(f"header.{payload}.signature") is None


class TestReauth:
    def _entry(self, hass: HomeAssistant, unique_id: str) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=1,
            unique_id=unique_id,
            data=make_oauth_config_data(token={**FAKE_TOKEN, "access_token": "stale"}),
        )
        entry.add_to_hass(hass)
        return entry

    async def _run(
        self,
        hass: HomeAssistant,
        entry: MockConfigEntry,
        account_id: str | None,
    ):
        mock_impl = _mock_implementation()
        mock_impl.async_resolve_external_data = AsyncMock(
            return_value={**FAKE_TOKEN, "access_token": _jwt(account_id)}
        )
        with patch(
            "custom_components.obi_energy_tracker.config_flow."
            "KeycloakOAuth2Implementation",
            return_value=mock_impl,
        ):
            result = await entry.start_reauth_flow(hass)
            assert result["step_id"] == "reauth_confirm"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={}
            )
            if (
                result["type"] is FlowResultType.FORM
                and result["step_id"] == "pick_implementation"
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={"implementation": DOMAIN}
                )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={"code": "new-auth-code"}
            )
        if result["type"] is FlowResultType.EXTERNAL_STEP_DONE:
            result = await hass.config_entries.flow.async_configure(result["flow_id"])
        return result

    async def test_reauth_updates_the_existing_entry(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        _mock_bridges(mock_responses)
        entry = self._entry(hass, "acc-1")

        result = await self._run(hass, entry, "acc-1")

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert len(hass.config_entries.async_entries(DOMAIN)) == 1
        assert entry.data["token"]["access_token"] == _jwt("acc-1")

    async def test_reauth_rejects_another_account(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        _mock_bridges(mock_responses)
        entry = self._entry(hass, "acc-1")

        result = await self._run(hass, entry, "acc-2")

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_account"
        assert entry.data["token"]["access_token"] == "stale"

    async def test_reauth_rejects_a_token_without_account_id(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        _mock_bridges(mock_responses)
        entry = self._entry(hass, "acc-1")

        result = await self._run(hass, entry, None)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_account"
        assert entry.data["token"]["access_token"] == "stale"


class TestLogout:
    async def _entry(self, hass: HomeAssistant):
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=1,
            data=make_oauth_config_data(token={**FAKE_TOKEN}),
        )
        entry.add_to_hass(hass)
        return entry

    async def test_options_flow_shows_confirmation(self, hass: HomeAssistant) -> None:
        entry = await self._entry(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

    async def test_confirmation_revokes_and_aborts(self, hass: HomeAssistant) -> None:
        entry = await self._entry(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)

        with (
            patch(
                "custom_components.obi_energy_tracker.config_flow.async_revoke_session",
                new=AsyncMock(),
            ) as revoke,
            patch.object(entry, "async_start_reauth") as start_reauth,
        ):
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], user_input={}
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "logged_out"
        revoke.assert_awaited_once()
        start_reauth.assert_called_once_with(hass)

    async def test_revoke_posts_refresh_token(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        entry = await self._entry(hass)
        mock_responses.post(OAUTH2_LOGOUT_URL, status=204)

        await async_revoke_session(hass, entry)

        method, url, data, _ = mock_responses.mock_calls[0]
        assert method == "POST"
        assert str(url) == OAUTH2_LOGOUT_URL
        assert data["refresh_token"] == FAKE_TOKEN["refresh_token"]
        assert data["client_id"] == OAUTH2_CLIENT_ID

    async def test_revoke_without_refresh_token_is_noop(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        entry = await self._entry(hass)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "token": {"access_token": "a"}}
        )

        await async_revoke_session(hass, entry)

        assert mock_responses.mock_calls == []

    async def test_revoke_tolerates_error_response(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        entry = await self._entry(hass)
        mock_responses.post(
            OAUTH2_LOGOUT_URL,
            status=400,
            text="invalid_grant",
        )

        await async_revoke_session(hass, entry)

        assert mock_responses.call_count == 1

    async def test_revoke_tolerates_connection_error(
        self, hass: HomeAssistant, mock_responses
    ) -> None:
        entry = await self._entry(hass)
        mock_responses.post(
            OAUTH2_LOGOUT_URL,
            exc=aiohttp.ClientError("offline"),
        )

        await async_revoke_session(hass, entry)


class TestKeycloakImplementation:
    def _impl(self, hass: HomeAssistant) -> KeycloakOAuth2Implementation:
        return KeycloakOAuth2Implementation(hass)

    def test_redirect_uri_uses_my_home_assistant(self, hass: HomeAssistant) -> None:
        hass.config.components.add("my")

        assert self._impl(hass).redirect_uri == MY_AUTH_CALLBACK_PATH

    def test_redirect_uri_falls_back_to_frontend_base(
        self, hass: HomeAssistant
    ) -> None:
        token = current_request.set(
            MagicMock(headers={"HA-Frontend-Base": "https://obi.duckdns.org"})
        )
        try:
            assert self._impl(hass).redirect_uri == (
                "https://obi.duckdns.org/auth/external/callback"
            )
        finally:
            current_request.reset(token)

    def test_code_challenge_is_s256_of_verifier(self, hass: HomeAssistant) -> None:
        impl = self._impl(hass)
        verifier = impl.extra_token_resolve_data["code_verifier"]

        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        assert impl.extra_authorize_data["code_challenge"] == expected
        assert impl.extra_authorize_data["code_challenge_method"] == "S256"

    def test_verifier_length_within_rfc7636_bounds(self, hass: HomeAssistant) -> None:
        verifier = self._impl(hass).extra_token_resolve_data["code_verifier"]

        assert 43 <= len(verifier) <= 128

    def test_verifiers_differ_between_instances(self, hass: HomeAssistant) -> None:
        first = self._impl(hass).extra_token_resolve_data["code_verifier"]
        second = self._impl(hass).extra_token_resolve_data["code_verifier"]

        assert first != second

    def test_client_id_and_urls(self, hass: HomeAssistant) -> None:
        impl = self._impl(hass)

        assert impl.client_id == OAUTH2_CLIENT_ID
        assert impl.domain == DOMAIN
        assert impl.authorize_url == (
            "https://auth.obi.com/auth/realms/energy-tracker-clients"
            "/protocol/openid-connect/auth"
        )
        assert impl.token_url.endswith("/protocol/openid-connect/token")
