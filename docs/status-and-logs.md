# Job status, archived logs, and the MLSpace Public API

## Logs without a live job

`cryri logs HASH` first looks in `~/.mlspace-logs`. Override the archive root with `--log-dir PATH` or `CRYRI_LOG_DIR`. The standard layout is `JOB/retry.NNN/rank.N/stdout` and `stderr`; regular `.log` files under the job directory are also recognized. Symlinked files and directories outside the root are not read. An exact archive name wins; ambiguous partial hashes fail with an actionable error.

```bash
cryri logs JOB_NAME
cryri logs PARTIAL_HASH --head 40 --tail 200
cryri logs JOB_NAME --source local --log-dir /path/to/archive
cryri logs JOB_NAME --source api --tail 100
cryri logs JOB_NAME --full --raw > job-output.txt
cryri logs JOB_NAME --follow
```

The default local preview shows the first 40 and last 200 physical lines per file, subject to a **64 KiB total data budget** and at most **8 files**. It reads bounded prefixes and suffixes with file seeks; it does not scan gigabytes to count omitted lines. Omission notices give the skipped byte count and identify omitted files. Retry directories are ordered newest first and ranks numerically. Small files are printed once, including when their head and tail overlap. A line larger than the budget may be clipped; UTF-8 fragments at preview boundaries are decoded with replacement characters.

Use `--head`, `--tail`, `--max-bytes`, and `--max-files` to tune previews. `--full` removes all preview limits and streams every matching file in bounded chunks. `--raw` preserves timestamps, MPI prefixes, whitespace, and normally filtered noise; source/file headers and omission notices remain visible. Otherwise the existing MPI/timestamp/noise cleaner is applied after selecting the preview.

`--source auto` (default) falls back to HTTP if there are no local files. A full job name or locally resolved partial hash does not require a job-list API request. `--source local` fails clearly if no archive exists; `--source api` bypasses local files. Without a hash, the selector includes finished jobs; local selection is also available when the API is unavailable.

The HTTP API provides a tail parameter, not random access to file headers. Therefore API previews request the most recent `--tail` lines (or `--head` if larger), bounded by `--max-bytes`; the local head/tail split does not apply. `--full` streams the API response from the beginning. Preview responses are closed as soon as their byte limit is reached.

`--follow` prints an initial local preview, then follows appended data, new rank/retry files, file replacement, and truncation. Followed output is streamed without preview limits. It preserves UTF-8 and incomplete lines across polls. It stops when the API reports a terminal status, or on Ctrl-C. Archive inactivity alone is not proof that a job stopped; a stale or unavailable gateway may require Ctrl-C. The optional legacy `client_lib` API retains that library's streaming behavior; local previews work independently of it.

## Status is a report, not a health check

```bash
cryri status JOB_NAME
cryri status JOB_NAME --json
cryri inspect JOB_NAME
cryri inspect JOB_NAME --history
cryri status JOB_NAME --history --json
cryri params JOB_NAME
cryri nodes JOB_NAME
```

The notebook gateway's single-job contract is **GET `/job_status` with query parameters `job` and `region`**. Its job list and status reports can lag behind platform termination. Status output identifies its source and gateway output warns about this limitation; JSON includes `status_source`. It never converts missing logs, an API timeout, a traceback, or a silent archive into a guessed Failed state. Terminated, Stopped, Deleted, Succeeded, and the other documented terminal states stop followers and are excluded from the interactive kill selector.

`inspect` prints metadata as JSON. `params` retrieves launch parameters and masks secret-named fields; `nodes` retrieves node information. `--history` resolves the Public API's actual `uid` and fetches v3 phase/history, including available platform messages. The uid is not inferred from a Kubernetes job-name suffix. Unsupported endpoints and authentication/permission errors are reported rather than silently falling back to a misleading status.

## Optional Public API backend

The default backend is `auto`: use the current Public API when its credentials are configured; otherwise retain notebook gateway access. Choose explicitly with global `--backend gateway|public|auto` (before the subcommand) or `CRYRI_API_BACKEND`. An incomplete Public API configuration is an error; use explicit gateway mode if only that access is intended. `CRYRI_USE_CLIENT_LIB=1` continues selecting the older optional SDK for its existing operations.

The Public API accepts standard developer credentials from the process environment or an optional personal TOML file:

- `MLSPACE_CLIENT_ID`
- `MLSPACE_CLIENT_SECRET`
- `MLSPACE_WORKSPACE_ID`
- `MLSPACE_API_KEY`, falling back to the existing `GWAPI_KEY`
- Optional `MLSPACE_API_URL`: HTTPS API origin, default `https://api.ai.cloud.ru`

Set these through your normal secret-management mechanism or the personal configuration below. Cryri does not create credential files, export file credentials into the process/job environment, or persist access tokens. Tokens are cached in memory, refreshed conservatively, and refreshed once on an explicit 401. Authentication errors do not echo credentials. No additional SDK dependency is required.

### Personal configuration on shared notebooks

Cryri never automatically reads the shared account's `~/.config`, `~/.cryri`, `XDG_CONFIG_HOME`, or a project's `.env`. It mirrors the notebook's directory-based user selection: from `/home/jovyan/alice/project`, the one automatic candidate is `/home/jovyan/alice/.config/cryri/credentials.toml`. Nested working directories select the same personal root. At `/home/jovyan`, outside that tree, or inside its hidden top-level directories, no personal file is inferred. Paths use the resolved working directory; symlinks to a project therefore select its physical personal root. Automatically selected files cannot resolve outside their personal root.

Choose a different location explicitly:

```bash
# Choose a personal root when calling from a shared project or elsewhere.
cryri --user-home /home/jovyan/alice config
cryri --user-home /home/jovyan/alice submit run.yaml

# Select an alternative personal credentials file.
cryri --config /home/jovyan/alice/.config/cryri/alternate.toml submit run.yaml

# Equivalent persistent overrides; add one to your own shell setup if desired.
export CRYRI_USER_HOME=/home/jovyan/alice
# Or select a specific file:
export CRYRI_CONFIG=/home/jovyan/alice/.config/cryri/credentials.toml
```

`--config` takes precedence over `CRYRI_CONFIG`. Either file selector takes precedence over personal-root selection. Otherwise `--user-home` takes precedence over `CRYRI_USER_HOME`, then Cryri infers the personal root from the working directory. It never searches other people's directories or walks up the project tree for credentials. Explicit selectors also reject shared-root credential files and hidden directories such as `/home/jovyan/.config` or `/home/jovyan/.cryri`; portable explicit paths outside the notebook tree remain supported. No shell wrapper or cache changes are needed.

A missing automatically selected file (including one chosen through `--user-home`) is optional: the existing environment/gateway behavior continues. A missing explicitly selected `--config`/`CRYRI_CONFIG` file is an error, so a typo cannot silently select a different account. Empty files are allowed. Malformed files, unsupported fields, and partial Public API credentials produce an actionable error when those API settings are used. `--backend gateway` bypasses file authentication for gateway commands. Local log reads, command help, and version output do not require valid Public API credentials.

An optional file looks like this (replace the placeholders locally):

```toml
[mlspace]
client_id = "YOUR_KEY_ID"
client_secret = "YOUR_KEY_SECRET"
workspace_id = "YOUR_WORKSPACE_ID"
api_key = "YOUR_WORKSPACE_API_KEY"
# api_url = "https://api.ai.cloud.ru"

[cryri]
backend = "auto"  # auto, public, or gateway
```

The TOML field names map to the `MLSPACE_*` variables above; `backend` maps to `CRYRI_API_BACKEND`. Environment variables override file fields, including an explicitly empty value; global `--backend` overrides both. Gateway settings (`GWAPI_ADDR`, `GWAPI_KEY`, namespace) remain in the environment. `MLSPACE_API_KEY` may still fall back to `GWAPI_KEY`. Authentication uses the identity associated with the keys; a directory name does not impersonate a platform username.

Use `cryri config` to inspect the selected path, selected backend, and whether fields come from the file or environment. It prints field presence and provenance, never credential values, and makes no network requests. CLI invocations keep their configuration separate; file values are reloaded for the next invocation. The file is size-limited to 64 KiB and parsed as TOML, never executed as shell code. TOML support uses the standard library on Python 3.11+ and `tomli` on older supported Python versions.

Keep your real file out of Git and restrict its permissions, for example `chmod 600 /home/jovyan/alice/.config/cryri/credentials.toml`. Directory-based selection prevents accidental configuration mixing; people sharing the same Unix account still share that account's file access.

Public API operations use the published contracts:

| Operation | Endpoint |
| --- | --- |
| Submit / paginated list | POST / GET `/public/v2/jobs` |
| Job metadata / terminate | GET / DELETE `/public/v2/jobs/{job_name}` |
| Logs | GET `/public/v2/jobs/{job_name}/logs` |
| Launch parameters | GET `/public/v2/jobs/{job_name}/params` |
| Nodes | GET `/public/v2/jobs/{job_name}/nodes` |
| Instance configurations | GET `/public/v2/configs?cluster_type=mt` |
| Detailed phase and history | GET `/public/v3/jobs/{uid}` |

Known lowercase status values are normalized for existing CLI filters and styling. Job-list pagination preserves metadata instead of dropping it. Public API list timestamps (`created_dt`) are normalized to numeric `created_at` values so Created cells, latest-N ordering, and `--since` filters remain consistent. Read requests retry transient gateway errors; submission and termination requests are never replayed automatically after ambiguous transport failures or server errors. There are no automatic job restarts or GPU-idle-policy changes.

Contracts: [vendor OpenAPI specification](https://api.ai.cloud.ru/public/v2/redoc), [machine-readable schema](https://api.ai.cloud.ru/public/v2/openapi.json), and [vendor CLI/SDK](https://github.com/cloud-ru/ml-cli).
