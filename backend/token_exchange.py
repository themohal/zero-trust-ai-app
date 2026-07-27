"""
ACT 3 (agent identity): trade Alice's full token for a narrow, agent-scoped one.

This is an RFC 8693 token exchange against Keycloak. The backend never reuses
Alice's full token to invoke tools; instead it exchanges it for a NEW token that
carries ONLY the `tools:invoke` role (see the `tools-invoke` client scope in
keycloak/realm-export.json). A hijacked agent holding that scoped token can call
the tool endpoint - and nothing else Alice could do.

Two ways to authenticate the exchange to Keycloak:

  1. client_secret  (AUTH_MODE=secret, the default) - reliable, works out of the
     box. The agent proves it is the `backend-agent` client with a shared secret.

  2. SPIFFE JWT-SVID (AUTH_MODE=svid) - the zero-trust upgrade. The agent fetches
     a short-lived, cryptographically-signed identity document from the local
     SPIRE agent and presents it as an RFC 7521 client assertion. No static
     secret exists anywhere. This requires the community
     `spiffe-svid-client-authenticator` Keycloak SPI to be installed and the
     backend-agent client reconfigured to accept it (see README, Act 3, Part C).

Both paths hit the same token endpoint with the same subject_token; only the
client-authentication parameters differ.
"""

import os

import httpx
from fastapi import HTTPException

REALM = os.environ.get("KC_REALM", "zerotrust")

# The backend dials Keycloak over the internal Docker hostname.
KC_INTERNAL_URL = os.environ.get("KC_INTERNAL_URL", "http://localhost:8081")
TOKEN_ENDPOINT = f"{KC_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/token"

AGENT_CLIENT_ID = os.environ.get("AGENT_CLIENT_ID", "backend-agent")
AGENT_CLIENT_SECRET = os.environ.get("BACKEND_AGENT_SECRET", "backend-agent-secret")

# "secret" (default) or "svid".
AUTH_MODE = os.environ.get("AUTH_MODE", "secret").lower()

# SPIFFE settings (only used when AUTH_MODE=svid).
SPIFFE_SOCKET = os.environ.get("SPIFFE_WORKLOAD_API", "unix:///opt/spire/sockets/agent.sock")
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


def _fetch_live_svid_jwt() -> str:
    """Fetch a fresh JWT-SVID from the local SPIRE agent (AUTH_MODE=svid only).

    Imported lazily so the default (secret) path has no hard dependency on the
    `spiffe` package / a running SPIRE agent.
    """
    # The `spiffe` package reads the socket path from SPIFFE_ENDPOINT_SOCKET.
    os.environ.setdefault("SPIFFE_ENDPOINT_SOCKET", SPIFFE_SOCKET)
    try:
        from spiffe import WorkloadApiClient
    except ImportError as e:  # pragma: no cover - only hit in svid mode
        raise HTTPException(
            status_code=500,
            detail=f"AUTH_MODE=svid requires the 'spiffe' package to be installed: {e}",
        )

    with WorkloadApiClient() as client:
        # Audience must be the Keycloak client the SVID authenticates as.
        svid = client.fetch_jwt_svid(audience={AGENT_CLIENT_ID})
        return svid.token


async def exchange_for_scoped_token(user_access_token: str, requested_scope: str) -> dict:
    """Exchange Alice's token for one scoped to `requested_scope` (e.g. tools-invoke)."""
    data = {
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "client_id": AGENT_CLIENT_ID,
        "subject_token": user_access_token,
        "subject_token_type": ACCESS_TOKEN_TYPE,
        "requested_token_type": ACCESS_TOKEN_TYPE,
        # Optional client scope - this is what narrows the roles in the new token.
        "scope": requested_scope,
    }

    if AUTH_MODE == "svid":
        data["client_assertion_type"] = CLIENT_ASSERTION_TYPE
        data["client_assertion"] = _fetch_live_svid_jwt()
    else:
        data["client_secret"] = AGENT_CLIENT_SECRET

    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_ENDPOINT, data=data)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Token exchange failed ({AUTH_MODE}): {resp.status_code} {resp.text}",
        )
    return resp.json()
