# embed-cli — shell/Docker fallback

When the Openbridge MCP isn't attached, the same workflows run via
`embed-cli` (a Bash CLI in `/Users/thomas/Github/embedded-api/embed-cli`)
or its Docker image (`openbridge/embed-cli`). Same APIs, no sandbox.

Use embed-cli when:

- The MCP isn't available in the user's environment
- The user explicitly asks for it (CI scripts, ops runbooks)
- A workflow needs CSV batch (`jobs batch -f <file>`) — not exposed via
  MCP

Otherwise prefer the MCP — it's better at multi-step planning, returns
structured envelopes, and respects per-tenant auth.

## Auth

Same refresh tokens (`xxx:yyy`). Two ways to pass:

```bash
# 1. Env var on the call
REFRESH_TOKEN="xxx:yyy" ./bin/embed-cli jobs list --subscription 128853

# 2. Sourced config file
echo 'REFRESH_TOKEN="xxx:yyy"' > config.env
chmod 600 config.env
CONFIG_FILE="$(pwd)/config.env" ./bin/embed-cli jobs list --subscription 128853
```

Or in Docker:

```bash
docker run --rm \
  -e "REFRESH_TOKEN=$OPENBRIDGE_REFRESH_TOKEN" \
  openbridge/embed-cli jobs list --subscription 128853

# With config file mount
docker run --rm \
  -v "$(pwd)/config.env:/app/config.env:ro" \
  openbridge/embed-cli jobs list --subscription 128853
```

JWT caching: mount `~/.embed-cli` (or a Docker volume `embed-cli-cache`)
to `/app/cache` so repeated calls reuse the JWT instead of re-exchanging
the refresh token. Set permissions to `700` on the host directory.

## Common commands

### Jobs

```bash
./bin/embed-cli jobs list --subscription 128853
./bin/embed-cli jobs list --subscription 128853 --last-days 7
./bin/embed-cli jobs list --subscription 128853 --stage 1004

./bin/embed-cli jobs create \
  --start 2024-01-01 --end 2024-01-07 \
  --subscription 128853 --stage 1004
```

### Batch backfill (CSV)

```bash
# CSV format: date,subscription_id[,stage_id]
cat > backfill.csv <<'EOF'
date,subscription_id,stage_id
2024-01-01,128853,1004
2024-01-02,128853,1004
2024-01-03,128853,1004
EOF

./bin/embed-cli jobs batch -f backfill.csv

# Or via Docker:
docker run --rm \
  -e "REFRESH_TOKEN=$OPENBRIDGE_REFRESH_TOKEN" \
  -v "$(pwd)/backfill.csv:/app/backfill.csv:ro" \
  openbridge/embed-cli jobs batch -f /app/backfill.csv
```

### Healthchecks

```bash
./bin/embed-cli health check
./bin/embed-cli health check --subscription 128853
./bin/embed-cli health check --last-days 2
./bin/embed-cli health check --page 5
```

### Subscriptions

```bash
./bin/embed-cli subscription list
./bin/embed-cli subscription list --status active --page-size 50
./bin/embed-cli subscription update --id 128853 --status active
./bin/embed-cli subscription update --id 128853 --storage-group 1289
```

### Stages

```bash
./bin/embed-cli stages list --product 70
```

### Identities

```bash
./bin/embed-cli identity list
./bin/embed-cli identity list --invalid 1
./bin/embed-cli identity list --invalidated-after "2024-01-01T00:00:00"
./bin/embed-cli identity get 4832
```

### User info

```bash
./bin/embed-cli user info
./bin/embed-cli user id
```

## Output

Every command emits JSON to stdout. Pipe to `jq` for shaping:

```bash
./bin/embed-cli subscription list --status active \
  | jq '.data[] | {id: .id, product: .attributes.product_name}'
```

## Logging & debugging

```bash
LOG_LEVEL=DEBUG ./bin/embed-cli health check
LOG_FILE=/tmp/embed-cli.log ./bin/embed-cli jobs list --subscription 128853
```

Log levels: `DEBUG`, `INFO`, `WARN`, `ERROR`.

Retry tuning:

```bash
RETRY_COUNT=5 SLEEP_DURATION=2 ./bin/embed-cli jobs list --subscription 128853
```

## Pitfalls (mirroring the MCP path)

- Wildcard backfills via `jobs create` without `--stage` queue every
  stage. Pass `--stage <id>` or use a CSV with a `stage_id` column.
- Date strings must be ISO `YYYY-MM-DD`.
- `subscription update` accepts only specific attributes — `--status`,
  `--storage-group`, others. For full edits use the MCP
  `update_subscription` with a JSON:API attributes object.
- Tokens in shell history. Pass via env file (`CONFIG_FILE`) or a
  password manager, not as a literal arg.

## When to prefer the MCP

The MCP wins for:

- Multi-step plans (find product → list tables → get schema → run SQL)
- Validated query execution (`validate_query` / `execute_query` —
  embed-cli has no equivalent)
- Structured error envelopes (`error_kind`, `_meta.retry_after_seconds`)
- Multi-tenant deployments (per-client Bearer tokens)

The CLI wins for:

- CSV batch backfills (the one workflow not in the MCP)
- Ops automation in shell scripts / cron / CI
- Environments where attaching an MCP is more friction than running a
  container
