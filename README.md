# hf2ms

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Modal](https://img.shields.io/badge/Modal-serverless-green.svg)](https://modal.com)

Cloud-to-cloud ML repo migration via [Modal](https://modal.com). Transfer models and datasets between HuggingFace and ModelScope without downloading anything locally.

## How It Works

Your machine sends the command. Modal containers do all the work.

```
Your Machine              Modal Container (ephemeral)              Platforms
┌──────────┐  modal run  ┌──────────────────────────┐  API calls  ┌──────────┐
│ Terminal  │ ─────────> │                          │ <─────────> │ HF Hub   │
│ or Claude │            │  Download from source    │             └──────────┘
│ Code      │            │  Upload to destination   │  API calls  ┌──────────┐
└──────────┘             │  Verify SHA256 hashes    │ <─────────> │ MS Hub   │
                         └──────────────────────────┘             └──────────┘
                              ↕ spins up, transfers, shuts down
```

No files touch your machine. Modal provisions a container, downloads from the source platform's API, uploads to the destination, verifies SHA256 integrity, and destroys the container. For large repos, it fans out to up to 100 parallel containers.

## Features

- **Zero local storage** — everything transfers cloud-to-cloud on Modal containers
- **Parallel chunked migration** — splits large repos across up to 100 containers for TB-scale transfers
- **SHA256 verification** — LFS file hashes checked after upload (skips platform-generated files)
- **Auto git fallback** — if the Hub API fails (403, storage lock), seamlessly retries via `git clone` + `git lfs pull`
- **Visibility preservation** — private repos stay private on the destination
- **Fire & forget** — detached mode lets migrations continue after you disconnect
- **Bidirectional** — HuggingFace → ModelScope and ModelScope → HuggingFace

## Quick Start

### 1. Install Modal

```bash
pip install modal
modal token new
```

### 2. Set Tokens

```bash
cp .env.example .env
# Fill in your tokens, then:

# bash/zsh
export $(cat .env | xargs)

# PowerShell
Get-Content .env | ForEach-Object { if ($_ -match '^([^#].+?)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }
```

| Variable | Where to Get It |
|----------|----------------|
| `HF_TOKEN` | https://huggingface.co/settings/tokens (read + write) |
| `MODAL_TOKEN_ID` | `modal token new` or https://modal.com/settings |
| `MODAL_TOKEN_SECRET` | Same as above |
| `MODELSCOPE_TOKEN` | https://modelscope.ai/my/myaccesstoken |
| `MODELSCOPE_DOMAIN` | Optional. `modelscope.ai` for international (default: `modelscope.cn`) |

### 3. Validate

```bash
python scripts/validate_tokens.py
```

### 4. Smoke Test

```bash
modal run scripts/modal_migrate.py::hello_world
```

If this prints SDK versions, you're good to go.

## Usage

### Single Repo

```bash
# HuggingFace → ModelScope (auto-detect type)
modal run scripts/modal_migrate.py::main --source "username/my-model" --to ms

# ModelScope → HuggingFace
modal run scripts/modal_migrate.py::main --source "damo/text-to-video" --to hf --repo-type model

# Custom destination name
modal run scripts/modal_migrate.py::main --source "username/my-model" --to ms --dest "OrgName/model-v2"

# Dataset
modal run scripts/modal_migrate.py::main --source "username/my-dataset" --to ms --repo-type dataset
```

> **Windows**: Prefix commands with `PYTHONIOENCODING=utf-8` to avoid Unicode errors from Modal CLI.

### Parallel Mode (Large Repos)

For repos over ~10 GB, parallel mode splits the transfer across multiple containers. Each container clones the repo structure, downloads only its assigned files, and uploads them independently.

```bash
# Parallel with default 20 GB chunks
modal run scripts/modal_migrate.py::main --source "org/large-dataset" --to ms --parallel

# Custom chunk size (in GB)
modal run scripts/modal_migrate.py::main --source "org/large-dataset" --to ms --parallel --chunk-size 30

# Parallel dataset
modal run scripts/modal_migrate.py::main --source "org/my-dataset" --to ms --repo-type dataset --parallel
```

Chunk size auto-adjusts upward if the repo would exceed 100 containers. Parallel mode is currently HuggingFace → ModelScope only.

### Batch (Multiple Repos)

Each repo gets its own container, running in parallel via `starmap()`. Repos that already exist on the destination are automatically skipped.

```bash
modal run scripts/modal_migrate.py::batch \
  --source "user/model1,user/model2,user/model3" \
  --to ms --repo-type model
```

### Detached Mode (Fire & Forget)

Add `--detach` before the script path. The migration continues in Modal's cloud even after you close your terminal:

```bash
modal run --detach scripts/modal_migrate.py::main \
  --source "username/my-model" --to ms
```

Monitor detached runs:

```bash
modal app logs hf-ms-migrate      # stream logs
modal app list                    # see running/recent apps
modal app stop hf-ms-migrate     # cancel a running migration
```

Or check the [Modal dashboard](https://modal.com/apps).

## Options

### Single (`::main`)

| Flag | Description | Required |
|------|-------------|----------|
| `--source` | Source repo ID (`user/model` or `hf:user/model`) | Yes |
| `--to` | Destination: `hf` or `ms` | Yes* |
| `--repo-type` | `model`, `dataset`, or `space` (auto-detects if omitted) | No |
| `--dest` | Custom destination repo ID | No |
| `--parallel` | Use parallel chunked migration (multiple containers) | No |
| `--chunk-size` | Chunk size in GB for parallel mode (default: 20) | No |
| `--use-git` | Force git clone instead of Hub API for download | No |

\*Not required if source has a platform prefix.

### Batch (`::batch`)

| Flag | Description | Required |
|------|-------------|----------|
| `--source` | Comma-separated repo IDs | Yes |
| `--to` | Destination: `hf` or `ms` | Yes |
| `--repo-type` | `model`, `dataset`, or `space` (default: `model`) | No |
| `--use-git` | Force git clone for all repos | No |

## Supported Repo Types

| Type | HF → MS | MS → HF |
|------|---------|---------|
| Models | Yes | Yes |
| Datasets | Yes | Yes |
| Spaces | Skipped (warning) | N/A |

Spaces to ModelScope are skipped because ModelScope Studios can only be created via the web UI — the SDK has no support. To force-migrate space files as a model repo, use `--repo-type model`.

## How the Git Fallback Works

When `snapshot_download()` fails — 403 from storage-locked orgs or access errors wrapped in `LocalEntryNotFoundError` — hf2ms automatically retries using raw `git clone --depth=1` + `git lfs pull`. This bypasses Hub API restrictions because git-based access is always available. The fallback is seamless: same result, no user intervention needed. (404s for genuinely missing repos are not retried.)

You can also force git mode with `--use-git` for any migration.

## Claude Code Plugin

This repo is a [Claude Code plugin](https://docs.anthropic.com/en/docs/claude-code/plugins). Install it and use natural language:

```
> migrate username/my-model to ModelScope
> transfer damo/text-to-video to HuggingFace
> batch migrate my models to ModelScope
```

Or use the `/migrate` slash command for a guided workflow with token validation, destination confirmation, and run mode selection:

```
> /migrate username/my-model --to ms
> /migrate username/my-dataset --to ms --type dataset --detach
> /migrate username/my-model --to ms --parallel
```

## Benchmarks

All migrations are cloud-to-cloud via Modal. No local disk involved.

### Parallel Mode (chunked, multiple containers)

| Size | Files | Chunks | Duration |
|------|-------|--------|----------|
| 8.5 GB | 21 | 3 | 5m 50s |
| 156 GB | 1,048 | 11 | 46m 16s |
| 175 GB | 39 | 11 | 28m 49s |
| 392 GB | 59 | 32 | 1h 15m |
| 613 GB | 122 | 41 | 58m 4s |
| 898 GB | 184 | 60 | 53m 3s |
| 1.0 TB | 150 | 85 | 1h 1m |
| 3.3 TB | 678 | 113 | 2h 0m |

### Single Container

| Size | Files | Duration | Notes |
|------|-------|----------|-------|
| 163 MB | — | 18.2s | model, MS→HF |
| 2.2 GB | 7 | 14m 11s | dataset |
| 15.6 GB | 67 | 7m 30s | model |
| 58.5 GB | 16 | 19m 48s | dataset |

### Batch Mode (one container per repo)

| Repos | Total Size | Duration |
|-------|------------|----------|
| 17 models | ~189 GB | 43m 44s |

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Token errors | `python scripts/validate_tokens.py` |
| Modal errors | `modal token verify` |
| Repo not found | Check the repo ID on the source platform |
| 403 / storage locked | Automatic: falls back to git clone. Or use `--use-git` |
| Timeout on large repos | Use `--parallel` to split across containers |
| ModelScope upload fails | Check `MODELSCOPE_TOKEN` write permissions |
| Unicode errors (Windows) | Prefix with `PYTHONIOENCODING=utf-8` |
| SHA256 mismatch | Re-run the migration (network issue during upload) |

## Project Structure

```
scripts/
  modal_migrate.py        Modal app (remote functions + local entrypoints)
  validate_tokens.py      Token validation utility
  utils.py                Shared helpers (repo parsing, direction detection)

.claude-plugin/
  plugin.json             Claude Code plugin manifest

commands/
  migrate.md              /migrate slash command

skills/migrate/
  SKILL.md                          Natural language migration skill
  references/
    hub-api-reference.md            HuggingFace & ModelScope SDK reference
    verification-and-cleanup.md     Post-migration verification guide
```

## License

[MIT](LICENSE)
