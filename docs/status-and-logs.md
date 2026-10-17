# Job status, archived logs, and personal credentials

## Archived logs

`cryri logs HASH` prefers `~/.mlspace-logs`. Override the root with `--log-dir` or `CRYRI_LOG_DIR`. Archives use `JOB/retry.NNN/rank.N/stdout` and `stderr`; regular `.log` files are also supported. Exact names win over partial hashes; ambiguous matches fail. Logs can be read without a live job or job-list request.

```bash
cryri logs JOB_NAME
cryri logs HASH --head 40 --tail 200
cryri logs JOB_NAME --source local --log-dir /path/to/archive
cryri logs JOB_NAME --source api --tail 100
cryri logs JOB_NAME --full --raw > job-output.txt
cryri logs JOB_NAME --follow
```

The local preview shows the first **40** and last **200** physical lines per file, capped at **64 KiB total** across **8 files**. It seeks to bounded prefixes/suffixes instead of scanning whole files. Small files are printed once; notices identify skipped bytes and files. Newest retries come first, with ranks sorted numerically. Symlinks cannot expose files outside the archive root.

- `--head`, `--tail`, `--max-bytes`, and `--max-files` change preview limits.
- `--full` streams all files in bounded chunks without preview limits.
- `--raw` preserves timestamps, MPI prefixes, whitespace, and noise. File headers and omission notices remain.
- `--source auto` falls back to HTTP when local logs are absent. `local` requires an archive; `api` bypasses it.
- `--follow` prints a preview, then follows appends, retries, rotation, and truncation, preserving split UTF-8 and partial lines. It stops on terminal API status or Ctrl-C. Inactivity alone never implies failure.

The HTTP API supports tail requests, so API previews use the larger of `--head` and `--tail` as a tail count, with the same byte cap; `--full` requests output from the beginning. Preview boundaries can clip long lines or UTF-8 characters. Followed output is unlimited over time. The optional legacy `client_lib` API retains its own streaming behavior.

## Status and the Public API

```bash
cryri status JOB_NAME --json
cryri inspect JOB_NAME --history
cryri params JOB_NAME
cryri nodes JOB_NAME
```

Gateway status uses **GET `/job_status?job=NAME&region=REGION`**. Gateway lists/status can remain stale after platform termination; output identifies the source and warns that it does not establish liveness. Missing logs, API errors, silence, and application tracebacks never become guessed Failed states. Known terminal states stop followers and are excluded from interactive kill selection.

`inspect` prints metadata as JSON. `params` and `nodes` retrieve their corresponding Public API data and mask secret-named fields. `status --history` and `inspect --history` use the v2 response's actual `uid` to fetch v3 phase/history.

The default `auto` backend uses the Public API when configured, otherwise the notebook gateway. Select `--backend auto|gateway|public` before the subcommand, or set `CRYRI_API_BACKEND`. Partial Public API configuration is an error. `CRYRI_USE_CLIENT_LIB=1` preserves the optional older SDK for its existing operations.

| Operation | Endpoint |
| --- | --- |
| Submit / paginated list | POST / GET `/public/v2/jobs` |
| Metadata / terminate | GET / DELETE `/public/v2/jobs/{job_name}` |
| Logs / parameters / nodes | GET `/public/v2/jobs/{job_name}/{logs,params,nodes}` |
| Instance configurations | GET `/public/v2/configs?cluster_type=MT` |
| Phase and history | GET `/public/v3/jobs/{uid}` |

Job metadata is preserved; known status names and `created_dt` timestamps are normalized for existing filters and sorting. Reads retry transient errors. Submission and termination are never replayed after ambiguous transport/server failures. Tokens stay in memory and refresh once after an explicit 401; authentication errors do not echo secrets.

## Personal configuration on shared notebooks

From `/home/jovyan/alice/project`, Cryri looks only in `/home/jovyan/alice/.config/cryri/credentials.toml`. It uses the resolved working directory's first component below `/home/jovyan`, matching the notebook's shell wrapper. At the shared root, outside that tree, or in hidden top-level directories, no personal file is inferred. No shared-home, XDG, project `.env`, or other user's directory is searched.

```toml
[mlspace]
client_id = "YOUR_KEY_ID"
client_secret = "YOUR_KEY_SECRET"
workspace_id = "YOUR_WORKSPACE_ID"
api_key = "YOUR_WORKSPACE_API_KEY"
# api_url = "https://api.ai.cloud.ru"

[cryri]
backend = "auto"
```

Credential fields map to `MLSPACE_CLIENT_ID`, `MLSPACE_CLIENT_SECRET`, `MLSPACE_WORKSPACE_ID`, `MLSPACE_API_KEY`, and `MLSPACE_API_URL`. Environment variables override file fields, including explicitly empty values; `--backend` overrides both. Gateway settings remain in the environment, and `MLSPACE_API_KEY` may fall back to `GWAPI_KEY`.

```bash
cryri --user-home /home/jovyan/alice submit run.yaml
cryri --config /home/jovyan/alice/.config/cryri/alternate.toml submit run.yaml
cryri config
```

Selection priority is `--config`, `CRYRI_CONFIG`, then `--user-home`, `CRYRI_USER_HOME`, then the working directory. File selectors require the selected file to exist; personal-root/directory discovery is optional. Missing optional files and empty files preserve gateway access. Shared-root/hidden notebook credential locations are rejected even when explicitly selected, and automatic symlinks cannot escape the personal root. Explicit portable paths outside the notebook tree are supported.

`cryri config` shows the selected file, backend, and field sources without values or network calls. Files are parsed as TOML, never shell code; tokens and file credentials are not exported into job environments. Configuration is scoped to each CLI invocation. Malformed files fail safely when used; `--backend gateway` bypasses file authentication for gateway commands. Local log reads, help, and version output do not require valid credentials.

Keep real credentials out of Git and use restrictive permissions such as `chmod 600`. People sharing one Unix account still share its file access; directory selection prevents accidental account mixing.

## Submission descriptions

Authenticated Public API submissions preserve the generated job description. Gateway and legacy submissions infer the username from the **submit command's working directory**, independently of credential-root overrides. If that complete username is absent from the description, Cryri adds `[username] `:

- `/home/jovyan/alice/project` with `training` becomes `[alice] training`.
- Existing `[alice]`, `#alice`, or a standalone `alice` is preserved; `alice2` does not count.
- Outside a recognizable personal directory, descriptions stay unchanged.

Contracts: [OpenAPI](https://api.ai.cloud.ru/public/v2/redoc), [machine-readable schema](https://api.ai.cloud.ru/public/v2/openapi.json), and [vendor CLI/SDK](https://github.com/cloud-ru/ml-cli).
