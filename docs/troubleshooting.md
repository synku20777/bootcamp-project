# Troubleshooting

Read the reported error before you change the environment. Keep the `request_id` when an API request fails.

## Docker cannot connect

1. Start Docker Desktop or the Linux Docker service.
2. Wait until `docker info` shows a **Server** section.
3. Run setup with `--resume`.

Reinstalling Python packages does not repair a stopped Docker engine.

## An API route returns `503`

Read `error.code` and `request_id` in the response. Use the request identifier to find the matching API log.

Run:

```bash
docker compose ps
docker compose logs mongo redis api
```

Use this table to select the next check:

| Error code | Action |
| --- | --- |
| `cache_unavailable` | Check the Redis service and logs |
| `mongodb_unavailable` | Check MongoDB logs and stored credentials |
| `snowflake_configuration_invalid` | Compare variable names with `.env.example` |
| `snowflake_account_invalid` | Use `organization-account` for `SNOWFLAKE_ACCOUNT` |
| `snowflake_authentication_failed` | Check the Snowflake username and password |
| `snowflake_role_unauthorized` | Resume setup and check the API role grant |
| `snowflake_warehouse_unavailable` | Check `COVID_WH` and role `USAGE` |
| `snowflake_permission_denied` | Resume setup and restore project grants |
| `analytics_objects_missing` | Complete the Snowflake mart deployment |
| `context_data_unavailable` | Compare the committed and active WDI snapshot identifiers |
| `snowflake_network_unavailable` | Check internet, DNS, proxy, and firewall access |

Do not change the API role to `ACCOUNTADMIN` to avoid a permission error.

## The dashboard cannot reach the API

The dashboard container must use `http://api:8000`. Inside the container, `localhost` refers to the dashboard container.

Check the bridge request:

```bash
docker compose exec -T dashboard python -c "import sys, urllib.request; sys.stdout.write(urllib.request.urlopen('http://api:8000/health/live').read().decode())"
```

If the request fails, inspect the dashboard and API logs:

```bash
docker compose logs dashboard api
```

## MongoDB uses old credentials

MongoDB applies root credentials only when it creates the data directory. Restore the credentials that created the current volume.

Delete the volume only when local annotations are disposable. This operation cannot be reversed.

Run these commands only after you accept the data loss:

```bash
docker compose down
docker volume rm covid-platform_mongo_data
```

Run setup with `--resume` after the deletion. Setup does not delete this volume automatically.

## A required port is busy

The host uses ports `8000`, `8050`, and `27017`. Run the local doctor first:

```bash
uv run --locked python -m scripts.bootstrap doctor --local
```

On Windows, identify the process:

```powershell
Get-NetTCPConnection -LocalPort 8000
Get-Process -Id <OwningProcess>
```

On macOS or Linux, identify the process:

```bash
lsof -i :8000
ss -ltnp
```

Repeat the command for the reported port. Stop only a process that you recognize.

## World Bank publication fails

1. Restore the committed WDI CSV and manifest from Git.
2. Run setup with `--resume`.

Only the explicit `refresh` command contacts the World Bank API. A failed candidate cannot replace the active snapshot.

See [World Bank country context](architecture/world-bank-context.md) for publication and rollback rules.

## Setup stops before completion

Setup exits with status `130` after an interruption. It keeps containers, volumes, and resumable state.

On Windows, run:

```powershell
.\setup.ps1 --resume
```

On macOS or Linux, run:

```bash
./setup.sh --resume
```

Setup checks each completed step before it skips that step.

## A pre-commit hook changes files

1. Review the changes.
2. Stage the accepted files.
3. Run the hooks again.

```bash
git add <updated-files>
uv run pre-commit run --all-files
```

## The lockfile is out of date

After an intentional dependency change, run:

```bash
uv lock
uv lock --check
```

Commit both `pyproject.toml` and `uv.lock` after the check succeeds.
