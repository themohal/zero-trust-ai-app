# Zero-Trust AI App

A working, three-act **zero-trust authentication & authorization** architecture for a
Claude-like AI app. Every layer is enforced independently — a valid token proves
*who* you are but never *what you may do*, and the AI agent gets its **own** narrow
cryptographic identity instead of reusing the human's.

| Act | Question | Enforcer |
|-----|----------|----------|
| **1 — Authentication** | "Is this a real, logged-in human?" | Keycloak (OIDC + PKCE) + FastAPI JWT verification |
| **2 — Authorization**  | "I know who you are — what may you do?" | FastAPI, using realm roles inside the verified token |
| **3 — Agent Identity** | "The agent is not Alice. Give it its own, weaker identity." | SPIRE (SPIFFE workload identity) + Keycloak RFC 8693 token exchange |

The full narrative design lives in [`zero-trust-ai-app.txt`](./zero-trust-ai-app.txt).
This README is the **operational** guide: how to run and test it.

---

## Stack

- **Keycloak 26.2** — Identity Provider (OIDC + OAuth2). Standard Token Exchange
  (RFC 8693) is an *officially supported* feature here (it was a preview flag in v25).
- **PostgreSQL 16** — Keycloak persistence (survives restarts).
- **FastAPI (Python 3.12)** — the backend API + all token enforcement.
- **SPIRE 1.15.2** — SPIFFE workload identity (server + agent + OIDC discovery provider).
- **Docker Compose** — local orchestration.

## Project layout

```
zero-trust-ai-app/
├── docker-compose.yml              # Keycloak+Postgres+SPIRE+backend
├── keycloak/realm-export.json      # realm, roles, clients, user, downscoping scope
├── spire/
│   ├── server/server.conf
│   ├── agent/agent.conf
│   └── oidc-provider/{oidc-discovery-provider.conf, nginx.conf}
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── auth.py                     # Act 1 verify_token + Act 2 require_role
│   ├── token_exchange.py           # Act 3 RFC 8693 exchange (secret | svid)
│   └── main.py                     # the API endpoints
├── scripts/bootstrap-spire.sh      # one-shot SPIRE join + workload registration
└── zero-trust-ai-app.txt           # the design narrative
```

## Prerequisites

- Docker + Docker Compose v2 (`docker compose`, not `docker-compose`).
- `curl` and a browser.
- Optional: [`jwt.io`](https://jwt.io) or `jq` to inspect token claims.

> **Windows note:** the `curl` commands below use `\` line-continuation (bash / Git Bash).
> In PowerShell, put each command on one line or use a backtick `` ` `` to continue.
> The SPIRE Docker workload attestor needs a Linux Docker daemon — use **WSL2** or
> **Docker Desktop with the WSL2 backend**.

---

## Quick start (TL;DR)

```bash
# 1. bring up Keycloak (+db) and SPIRE, build the backend
docker compose up -d --build

# 2. one-time SPIRE join + register the backend workload
bash scripts/bootstrap-spire.sh

# 3. get Alice a token (see "Act 1" below), then:
curl http://localhost:8000/me -H "Authorization: Bearer $ALICE_TOKEN"
```

Everything below is the same thing, explained and testable act by act.

---

## Act 1 — Authentication

**Goal:** a real browser login yields a signed JWT, and FastAPI independently
verifies it (signature + issuer + audience + expiry).

### 1. Start the stack

```bash
docker compose up -d --build
docker compose logs -f keycloak      # wait for "Keycloak ... started"
```

Sanity-check the realm is serving OIDC:

```bash
curl http://localhost:8081/realms/zerotrust/.well-known/openid-configuration
```

Log in to the admin console at <http://localhost:8081> with `admin` / `admin`.
The `zerotrust` realm auto-imports on boot with the `frontend` and `backend-agent`
clients, the three roles, and user **alice / alice123**.

### 2. Log in as Alice (browser + PKCE)

A fixed test PKCE pair is provided for convenience:

- `code_verifier`:  `dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk`
- `code_challenge`: `E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM`

Paste this into a browser:

```
http://localhost:8081/realms/zerotrust/protocol/openid-connect/auth?client_id=frontend&response_type=code&scope=openid&redirect_uri=http://localhost:3000/callback&code_challenge_method=S256&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM
```

Log in as `alice` / `alice123`. It redirects to a (dead, expected-to-fail)
`localhost:3000/callback?code=...` URL. **Copy the `code` value immediately** —
auth codes are single-use and expire in ~30–60 s.

Exchange it for tokens right away:

```bash
curl -X POST http://localhost:8081/realms/zerotrust/protocol/openid-connect/token \
  -d "client_id=frontend" \
  -d "grant_type=authorization_code" \
  -d "code=PASTE_CODE_HERE" \
  -d "redirect_uri=http://localhost:3000/callback" \
  -d "code_verifier=dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
```

Save the `access_token` from the response:

```bash
export ALICE_TOKEN="eyJ...the access_token..."
```

### 3. Let FastAPI verify it

```bash
curl http://localhost:8000/me -H "Authorization: Bearer $ALICE_TOKEN"
# -> 200 {"message":"Token verified successfully","username":"alice",...}

curl http://localhost:8000/me
# -> 401 {"detail":"Missing bearer token"}

curl http://localhost:8000/me -H "Authorization: Bearer garbage.garbage.garbage"
# -> 401 {"detail":"Invalid token: ..."}
```

✅ **Act 1 done:** a real login produces a token, and the backend verifies it end
to end — rejecting anything forged, expired, or mis-scoped.

---

## Act 2 — Authorization

**Goal:** a valid token still can't do everything. Roles are enforced separately.

Alice's realm roles are `conversations:read`, `conversations:write`, **and**
`tools:invoke` (she needs `tools:invoke` so Act 3 can *narrow down to* it — a token
exchange can only ever narrow a permission the user already holds).

```bash
curl http://localhost:8000/conversations \
  -H "Authorization: Bearer $ALICE_TOKEN"
# -> 200 {"conversations":["chat-1","chat-2"],"user":"alice"}

curl -X POST http://localhost:8000/conversations/chat-1/messages \
  -H "Authorization: Bearer $ALICE_TOKEN"
# -> 200 {"status":"sent",...}

curl -X POST http://localhost:8000/conversations/chat-1/tools/invoke \
  -H "Authorization: Bearer $ALICE_TOKEN"
# -> 200 {"status":"tool invoked",...}
```

**401 vs 403:** `401` = "I don't know who you are." `403` = "I know exactly who you
are, and the answer is no." (You'll see the `403` in Act 3, where the *agent's*
narrow token is refused for actions outside its scope.)

✅ **Act 2 done:** roles carried in the verified token gate every action,
independent of authentication.

---

## Act 3 — Agent Identity

**Goal:** the backend never reuses Alice's full token to invoke tools. It gets its
own cryptographic identity from SPIRE, and uses an RFC 8693 **token exchange** to
trade Alice's token for a new one scoped to **only** `tools:invoke`.

### Part A — Why SPIRE

If the agent runs around with Alice's full token, a hijacked agent could do
anything Alice can. SPIRE gives the backend container its **own** identity —
`spiffe://zerotrust.local/backend-agent` — issued automatically from its Docker
label, with **no password and no static key**.

### Part B — Bootstrap SPIRE (one-time)

```bash
bash scripts/bootstrap-spire.sh
```

This script:
1. generates a join token and (re)starts `spire-agent` with it → node attestation,
2. registers the backend workload, matched purely by its Docker label
   (`org.zerotrust.name=backend-agent`).

Verify the workload has a real identity:

```bash
docker exec spire-server /opt/spire/bin/spire-server entry show
# -> shows spiffe://zerotrust.local/backend-agent

# fetch a JWT-SVID by hand to prove SPIRE will mint one for that label:
docker exec spire-agent /opt/spire/bin/spire-agent api fetch jwt \
  -audience backend-agent \
  -socketPath /opt/spire/sockets/agent.sock
```

The SPIRE **OIDC Discovery Provider** publishes SPIRE's public keys as a JWKS so
Keycloak (or anyone) can verify SVID signatures:

```bash
curl http://localhost:8443/.well-known/openid-configuration
curl http://localhost:8443/keys        # a real JWKS
```

### Part C — The token exchange

The backend runs with `AUTH_MODE=secret` by default, so **this works out of the
box** with no extra setup. Make sure you have a fresh `$ALICE_TOKEN` (Act 1), then:

```bash
curl -X POST http://localhost:8000/agent/invoke-tool \
  -H "Authorization: Bearer $ALICE_TOKEN"
```

Response contains a `scoped_token` and the result of the agent calling the tool
endpoint **with that scoped token**:

```json
{
  "message": "Exchanged Alice's token for a narrow agent token and invoked the tool with it",
  "scoped_token": "eyJ...",
  "tool_call_status": 200,
  "tool_call_body": {"status": "tool invoked", "conversation": "chat-1", "user": "alice"}
}
```

**Prove the narrowing is real.** Decode `scoped_token` at <https://jwt.io> — its
`realm_access.roles` shows **only** `["tools:invoke"]`. Now use it directly:

```bash
export SCOPED_TOKEN="eyJ...the scoped_token..."

curl -X POST http://localhost:8000/conversations/chat-1/tools/invoke \
  -H "Authorization: Bearer $SCOPED_TOKEN"
# -> 200  (it HAS tools:invoke)

curl -X POST http://localhost:8000/conversations/chat-1/messages \
  -H "Authorization: Bearer $SCOPED_TOKEN"
# -> 403  Missing required role: conversations:write
#    Alice's OWN token could send messages — her delegated agent token cannot.
```

✅ **Act 3 done:** even a fully compromised agent can only ever hold a token that
(a) is scoped to a single capability, (b) expires quickly, and (c) required a live
workload identity to obtain.

### Part D — (Optional) zero static secrets

`AUTH_MODE=secret` still authenticates the exchange with a shared secret — exactly
what SPIFFE exists to eliminate. The backend also ships an `AUTH_MODE=svid` path:
it fetches a live JWT-SVID from the local SPIRE agent and presents it as an
RFC 7521 client assertion, so **no static secret exists anywhere**.

This requires the community Keycloak SPI
[`spiffe-svid-client-authenticator`](https://github.com/christian-posta/spiffe-svid-client-authenticator)
to be built (`mvn clean package`), mounted into
`/opt/keycloak/providers/`, and the `backend-agent` client's Credentials tab set to
**SPIFFE SVID JWT** (Issuer `spiffe://zerotrust.local`, JWKS URL
`http://spire-oidc-nginx:8443/keys`). Then set `AUTH_MODE=svid` on the
`backend-agent` service and `docker compose up -d backend-agent`.

> This plugin is a community project and may lag newer Keycloak releases — review
> and build it against Keycloak 26.2 before relying on it. That is why `secret` is
> the default here.

---

## Resetting

```bash
docker compose down            # stop, keep data (Postgres + SPIRE volumes persist)
docker compose down -v         # nuke everything, including the realm + SPIRE state
```

After `down -v` you must re-run `bootstrap-spire.sh`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/me` returns `401 Invalid token: Invalid issuer` | Your token's `iss` must match `http://localhost:8081/realms/zerotrust`. Always log in via `localhost:8081`, not `keycloak:8080`. |
| Backend can't reach Keycloak | It dials `http://keycloak:8080` internally (`KC_INTERNAL_URL`); `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` makes that work. |
| `Token exchange failed: 400` | Ensure you're on Keycloak **26.2+** and the realm imported the `tools-invoke` client scope + `standard.token.exchange.enabled` on `backend-agent`. |
| SPIRE agent won't attest | Re-run `bootstrap-spire.sh`; confirm the Docker socket is mounted and you're on a Linux/WSL2 Docker daemon. |
| `entry create` says already exists | Harmless — the workload is already registered. |

## Known caveats (not production-hardened)

- `insecure_bootstrap = true` on the SPIRE agent is dev-only.
- No TLS between services locally — production needs TLS throughout.
- SPIRE server uses SQLite — production uses a real DB.
- The SPIFFE Keycloak SPI is a community project — review before production use.
