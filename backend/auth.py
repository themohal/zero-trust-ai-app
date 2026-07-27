"""
ACT 1 (authentication) + ACT 2 (authorization) enforcement for the backend.

Act 1 - verify_token(): independently proves a bearer token is a real, unexpired
        token that Keycloak actually signed for THIS realm and audience. We never
        trust a token just because it showed up.

Act 2 - require_role(): a separate, independent question - given a *valid* token,
        is the caller actually allowed to perform this action?

Docker gotcha handled here:
    A browser logs in against http://localhost:8081, so every token's `iss`
    claim is "http://localhost:8081/realms/zerotrust". But the backend, running
    inside the compose network, must fetch Keycloak's public keys over the
    internal hostname http://keycloak:8080. We therefore keep the ISSUER string
    (what we validate `iss` against) separate from the base URL we actually
    dial for JWKS.
"""

import os

import httpx
from jose import jwt
from jose.exceptions import JWTError
from fastapi import HTTPException, Request, Depends

REALM = os.environ.get("KC_REALM", "zerotrust")

# The public issuer that appears in the token's `iss` claim (browser-facing URL).
ISSUER = os.environ.get("KC_ISSUER", f"http://localhost:8081/realms/{REALM}")

# The base URL the backend actually dials to reach Keycloak. Inside Docker this
# is the internal service name (http://keycloak:8080); on the host it is 8081.
KC_INTERNAL_URL = os.environ.get("KC_INTERNAL_URL", "http://localhost:8081")
JWKS_URL = f"{KC_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/certs"

# The audience we require. Alice's browser token and the exchanged scoped token
# both carry aud="account" (the scoped token gets it via an audience mapper on
# the tools-invoke client scope - see keycloak/realm-export.json).
EXPECTED_AUDIENCE = os.environ.get("KC_AUDIENCE", "account")

_jwks_cache = None


async def get_jwks():
    """Fetch and cache Keycloak's public signing keys (JWKS)."""
    global _jwks_cache
    if _jwks_cache is None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(JWKS_URL)
            resp.raise_for_status()
            _jwks_cache = resp.json()
    return _jwks_cache


async def verify_token(request: Request) -> dict:
    """ACT 1: prove the token is genuine before trusting a single claim in it."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = auth_header.split(" ", 1)[1]
    jwks = await get_jwks()

    try:
        unverified_header = jwt.get_unverified_header(token)
        key = next(k for k in jwks["keys"] if k["kid"] == unverified_header["kid"])

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],     # signature: proves Keycloak signed it
            audience=EXPECTED_AUDIENCE,  # rejects tokens minted for another service
            issuer=ISSUER,            # rejects tokens from another realm/IdP
        )
        # `exp` is checked automatically by jose (raises ExpiredSignatureError).
        return claims
    except StopIteration:
        raise HTTPException(status_code=401, detail="Signing key not found (kid mismatch)")
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


def require_role(required_role: str):
    """ACT 2: given a verified token, gate the action on a realm role.

    401 = "I don't know who you are."      (verify_token failed)
    403 = "I know who you are, and no."    (this check failed)
    """

    def role_checker(claims: dict = Depends(verify_token)) -> dict:
        roles = claims.get("realm_access", {}).get("roles", [])
        if required_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required role: {required_role}",
            )
        return claims

    return role_checker
