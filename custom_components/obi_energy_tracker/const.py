from __future__ import annotations

DOMAIN = "obi_energy_tracker"

SCAN_INTERVAL_MINUTES = 5

MANUFACTURER = "OBI"
MODEL_BRIDGE = "ENERGY TRACKER Bridge"
MODEL_SENSOR = "ENERGY TRACKER Sensor"
MODEL_OUTLET = "ENERGY TRACKER Outlet"

KEY_CONSUMPTION = "consumption"
KEY_FEED_IN = "feed_in"

OAUTH2_CLIENT_ID = "home-assistant-user"
OAUTH2_SCOPES = "openid email profile offline_access"

_OPENID_CONNECT = (
    "https://auth.obi.com/auth/realms/energy-tracker-clients/protocol/openid-connect"
)

OAUTH2_AUTHORIZE_URL = f"{_OPENID_CONNECT}/auth"
OAUTH2_TOKEN_URL = f"{_OPENID_CONNECT}/token"
OAUTH2_LOGOUT_URL = f"{_OPENID_CONNECT}/logout"
