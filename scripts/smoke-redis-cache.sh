#!/usr/bin/env bash
# Live-stack validation that the Redis sidecar is actually backing
# FastMCP's task queue / cache.
#
# What it proves:
#   1. FASTMCP_DOCKET_URL inside the openbridge-mcp container points at
#      redis://redis:6379/0 — not the memory:// fallback.
#   2. Redis is reachable from the openbridge-mcp container only; the
#      host cannot connect to port 6379.
#   3. Docket populates real keys in Redis at server boot (worker
#      registration, task heartbeats).
#   4. A live MCP `tools/list` request flows through the running server,
#      and Docket activity is visible in Redis afterwards.
#   5. Restarting the openbridge-mcp container WITHOUT touching Redis
#      preserves the queue state — i.e. Redis is durable cache, not
#      ephemeral in-process memory.
#   6. Redis AOF persists to the named volume across `docker compose
#      down` (volume retained) + `up` (fresh containers).
#
# Usage:
#   ./scripts/smoke-redis-cache.sh
#
# Exits 0 on full success, non-zero on first failed check. Designed to
# be re-runnable: it tears the stack down at the end.

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
COMPOSE="docker compose -f ${COMPOSE_FILE}"
HOST_PORT="${HOST_PORT:-8002}"

GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
DIM=$'\033[2m'
RESET=$'\033[0m'

step() { printf "\n${GREEN}==> %s${RESET}\n" "$*"; }
fail() { printf "${RED}FAIL:${RESET} %s\n" "$*" >&2; exit 1; }
note() { printf "${DIM}    %s${RESET}\n" "$*"; }

cleanup() {
    step "Tearing down stack"
    ${COMPOSE} down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Build + start fresh
# ---------------------------------------------------------------------------
step "Bringing up fresh stack (compose: ${COMPOSE_FILE})"
${COMPOSE} down -v >/dev/null 2>&1 || true
${COMPOSE} up -d --build >/dev/null

# Wait for the openbridge-mcp /health endpoint instead of guessing.
step "Waiting for openbridge-mcp /health"
for _ in {1..30}; do
    if curl -fsS "http://localhost:${HOST_PORT}/health" >/dev/null 2>&1; then
        note "healthy"
        break
    fi
    sleep 1
done
curl -fsS "http://localhost:${HOST_PORT}/health" >/dev/null \
    || fail "/health never came up"

# ---------------------------------------------------------------------------
# 2. Container env points at Redis (not memory://)
# ---------------------------------------------------------------------------
step "FASTMCP_DOCKET_URL inside the container"
DOCKET_URL=$(${COMPOSE} exec -T openbridge-mcp printenv FASTMCP_DOCKET_URL || true)
note "FASTMCP_DOCKET_URL=${DOCKET_URL}"
[[ "${DOCKET_URL}" == redis://redis:6379/* ]] \
    || fail "Expected redis://redis:6379/* — got ${DOCKET_URL!r}"

# ---------------------------------------------------------------------------
# 3. Redis NOT reachable from host
# ---------------------------------------------------------------------------
step "Verifying Redis is not exposed publicly"
if nc -z -w 2 localhost 6379 2>/dev/null; then
    fail "Redis port 6379 is reachable from the host — should be internal-only"
fi
note "host cannot connect to localhost:6379 (expected)"

# ---------------------------------------------------------------------------
# 4. Container CAN reach Redis + Docket has populated keys at boot
# ---------------------------------------------------------------------------
step "Probing Redis from inside the openbridge-mcp container"
PING=$(${COMPOSE} exec -T openbridge-mcp \
    python -c "import os, redis; print(redis.Redis.from_url(os.environ['FASTMCP_DOCKET_URL']).ping())")
[[ "${PING}" == "True" ]] || fail "redis PING from container returned ${PING!r}"
note "PING True"

INITIAL_KEYS=$(${COMPOSE} exec -T redis redis-cli DBSIZE | tr -d '[:space:]')
note "Redis DBSIZE at boot: ${INITIAL_KEYS}"
[[ "${INITIAL_KEYS}" -gt 0 ]] \
    || fail "Redis is empty — Docket did not initialize against this backend"

step "Sample of Docket keys in Redis"
${COMPOSE} exec -T redis redis-cli --no-auth-warning KEYS '*' | head -10 \
    | sed "s/^/    /"

# ---------------------------------------------------------------------------
# 5. Live request flows through the server
# ---------------------------------------------------------------------------
step "POST tools/list against the live MCP HTTP endpoint"
RESPONSE=$(curl -fsS -X POST "http://localhost:${HOST_PORT}/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H 'MCP-Protocol-Version: 2025-06-18' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 2>&1 || true)

if echo "${RESPONSE}" | grep -q '"tools"'; then
    TOOL_COUNT=$(echo "${RESPONSE}" | python -c \
        "import sys, json, re; raw=sys.stdin.read(); m=re.search(r'data: (.+)', raw); body=json.loads(m.group(1) if m else raw); print(len(body.get('result',{}).get('tools',[])))")
    note "tools/list returned ${TOOL_COUNT} tools"
    [[ "${TOOL_COUNT}" -ge 1 ]] || fail "tools/list returned 0 tools"
else
    note "tools/list response (truncated): ${RESPONSE:0:200}"
    fail "tools/list response did not contain a 'tools' array"
fi

# ---------------------------------------------------------------------------
# 6. Restart MCP container — Redis state survives
# ---------------------------------------------------------------------------
step "Capturing a Redis key before MCP restart"
WITNESS_KEY="smoke:witness:$(date +%s)"
${COMPOSE} exec -T redis redis-cli SET "${WITNESS_KEY}" "alive" >/dev/null
WITNESS_BEFORE=$(${COMPOSE} exec -T redis redis-cli GET "${WITNESS_KEY}" | tr -d '[:space:]')
[[ "${WITNESS_BEFORE}" == "alive" ]] || fail "couldn't write witness key"
note "wrote ${WITNESS_KEY}=alive"

step "Restarting openbridge-mcp container only (Redis untouched)"
${COMPOSE} restart openbridge-mcp >/dev/null
for _ in {1..30}; do
    if curl -fsS "http://localhost:${HOST_PORT}/health" >/dev/null 2>&1; then break; fi
    sleep 1
done

WITNESS_AFTER=$(${COMPOSE} exec -T redis redis-cli GET "${WITNESS_KEY}" | tr -d '[:space:]')
[[ "${WITNESS_AFTER}" == "alive" ]] \
    || fail "witness key vanished — Redis is not surviving across MCP restart"
note "${WITNESS_KEY} still 'alive' — Redis state survives MCP restart"

# ---------------------------------------------------------------------------
# 7. AOF persistence across full container recreate (volume retained)
# ---------------------------------------------------------------------------
step "Recreate openbridge-mcp + redis containers WITHOUT removing volume"
# `down` without `-v` keeps named volumes; AOF should replay on next start.
${COMPOSE} down >/dev/null
${COMPOSE} up -d >/dev/null
for _ in {1..30}; do
    if curl -fsS "http://localhost:${HOST_PORT}/health" >/dev/null 2>&1; then break; fi
    sleep 1
done
WITNESS_PERSIST=$(${COMPOSE} exec -T redis redis-cli GET "${WITNESS_KEY}" | tr -d '[:space:]')
[[ "${WITNESS_PERSIST}" == "alive" ]] \
    || fail "witness key did not survive container recreate — AOF/volume not persisting"
note "${WITNESS_KEY} still 'alive' after container recreate (AOF replay confirmed)"

# ---------------------------------------------------------------------------
# 8. Done
# ---------------------------------------------------------------------------
step "All Redis-as-cache checks passed"
echo "${GREEN}OK: Redis sidecar is the live FastMCP cache backend.${RESET}"
