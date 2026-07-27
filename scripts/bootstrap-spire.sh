#!/usr/bin/env bash
# Bootstraps SPIRE for the zero-trust-ai-app demo:
#   1. generates a join token for the agent
#   2. (re)starts the agent with that token so it attests to the server
#   3. registers the backend-agent container as a SPIRE workload (Docker label)
#
# Safe to re-run. Requires the stack to be up: `docker compose up -d`.
set -euo pipefail

TRUST_DOMAIN="zerotrust.local"
AGENT_ID="spiffe://${TRUST_DOMAIN}/agent"
BACKEND_ID="spiffe://${TRUST_DOMAIN}/backend-agent"
OIDC_ID="spiffe://${TRUST_DOMAIN}/oidc-provider"

echo ">> Generating a join token for the agent..."
TOKEN="$(docker exec spire-server /opt/spire/bin/spire-server token generate \
  -spiffeID "${AGENT_ID}" | awk '/Token:/ {print $2}')"
echo "   token: ${TOKEN}"

echo ">> Restarting spire-agent with the join token..."
docker rm -f spire-agent >/dev/null 2>&1 || true
docker compose run -d --name spire-agent spire-agent \
  -config /opt/spire/conf/agent/agent.conf -joinToken "${TOKEN}"

echo ">> Waiting for node attestation..."
sleep 5
docker logs spire-agent 2>&1 | grep -i "attestation" || true

echo ">> Registering backend-agent workload (matched by Docker label)..."
docker exec spire-server /opt/spire/bin/spire-server entry create \
  -parentID "${AGENT_ID}" \
  -spiffeID "${BACKEND_ID}" \
  -selector "docker:label:org.zerotrust.name:backend-agent" || \
  echo "   (entry may already exist - that's fine)"

# The OIDC Discovery Provider is itself a Workload API client: it needs its own
# registered identity to fetch the trust bundle it serves as a JWKS on :8443.
# Without this, it logs "no identity issued" forever and /keys returns nothing.
echo ">> Registering spire-oidc-provider workload (matched by Docker label)..."
docker exec spire-server /opt/spire/bin/spire-server entry create \
  -parentID "${AGENT_ID}" \
  -spiffeID "${OIDC_ID}" \
  -selector "docker:label:org.zerotrust.name:spire-oidc-provider" || \
  echo "   (entry may already exist - that's fine)"

echo ">> Done. Verify with:"
echo "   docker exec spire-server /opt/spire/bin/spire-server entry show"
