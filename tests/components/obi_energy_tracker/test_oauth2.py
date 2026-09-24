from __future__ import annotations

from custom_components.obi_energy_tracker.oauth2 import oauth_token_provider

from .conftest import make_mock_oauth_session


class TestOAuthTokenProvider:
    async def test_returns_the_session_token(self) -> None:
        oauth_session = make_mock_oauth_session({"access_token": "abc"})

        assert await oauth_token_provider(oauth_session)() == "abc"

    async def test_refreshes_before_returning(self) -> None:
        oauth_session = make_mock_oauth_session()

        await oauth_token_provider(oauth_session)()

        oauth_session.async_ensure_token_valid.assert_awaited_once()

    async def test_picks_up_a_refreshed_token(self) -> None:
        oauth_session = make_mock_oauth_session({"access_token": "old"})
        provider = oauth_token_provider(oauth_session)

        assert await provider() == "old"
        oauth_session.token = {"access_token": "new"}

        assert await provider() == "new"
