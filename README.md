# HF2MS

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Modal](https://img.shields.io/badge/Modal-serverless-green.svg)](https://modal.com)

Migrate repos between HuggingFace and ModelScope using [Modal](https://modal.com) as cloud compute. No files touch your local machine — everything transfers cloud-to-cloud.

## How It Works

```
Your Machine                    Modal Container                    Platforms
┌──────────┐    modal run    ┌─────────────────┐    API calls    ┌──────────┐
│ Terminal  │ ──────────────> │ snapshot_download│ <────────────> │ HF Hub   │
│ or Claude │                │ upload_folder    │ <────────────> │ MS Hub   │
└──────────┘                 └─────────────────┘                 └──────────┘
```

Modal spins up an ephemeral container, downloads from the source platform, uploads to the destination, and shuts down. Your machine just sends the command.

## Quick Start

### 1. Install Modal

```bash
pip install modal
modal token new
```

### 2. Set Tokens

```bash
cp .env.example .env
# Fill in your tokens, then load them:

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

# Platform prefix instead of --to flag
modal run scripts/modal_migrate.py::main --source "hf:username/my-model" --to ms

# Dataset
modal run scripts/modal_migrate.py::main --source "username/my-dataset" --to ms --repo-type dataset
```

> **Windows**: Prefix commands with `PYTHONIOENCODING=utf-8` to avoid Unicode errors from Modal CLI.

### Batch (Parallel Containers)

Each repo gets its own Modal container and runs in parallel via `starmap()`. Repos that already exist on the destination are automatically skipped.

```bash
# Batch migrate models
modal run scripts/modal_migrate.py::batch \
  --source "user/model1,user/model2,user/model3" \
  --to ms --repo-type model

# Batch migrate datasets
modal run scripts/modal_migrate.py::batch \
  --source "user/dataset1,user/dataset2" \
  --to ms --repo-type dataset
```

### Detached Mode (Fire & Forget)

Add `--detach` before the script path. The migration continues in Modal's cloud even after you close your terminal:

```bash
# Single — detached
modal run --detach scripts/modal_migrate.py::main \
  --source "username/my-model" --to ms

# Batch — detached
modal run --detach scripts/modal_migrate.py::batch \
  --source "user/model1,user/model2,user/model3" \
  --to ms --repo-type model
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

\*Not required if source has a platform prefix.

### Batch (`::batch`)

| Flag | Description | Required |
|------|-------------|----------|
| `--source` | Comma-separated repo IDs | Yes |
| `--to` | Destination: `hf` or `ms` | Yes |
| `--repo-type` | `model`, `dataset`, or `space` (default: `model`) | No |

## Supported Repo Types

| Type | HF → MS | MS → HF |
|------|---------|---------|
| Models | Yes | Yes |
| Datasets | Yes | Yes |
| Spaces | Skipped (warning) | N/A |

Spaces to ModelScope are skipped because ModelScope Studios (their Spaces equivalent) can only be created via the web UI — the SDK has no support. To force-migrate space files as a model repo, use `--repo-type model`.

## Claude Code Plugin

This repo is also a [Claude Code plugin](https://docs.anthropic.com/en/docs/claude-code/plugins). Install it and use natural language:

```
> migrate username/my-model to ModelScope
> transfer damo/text-to-video to HuggingFace
> batch migrate my models to ModelScope
```

Or use the `/migrate` slash command for a guided workflow with token validation, destination confirmation, and detached mode option:

```
> /migrate username/my-model --to ms
> /migrate username/my-dataset --to ms --type dataset --detach
```

## Project Structure

```
.claude-plugin/plugin.json    Plugin manifest
commands/migrate.md           /migrate slash command
skills/migrate/SKILL.md       Natural language migration skill
scripts/modal_migrate.py      Modal app (5 remote functions + 2 entrypoints)
scripts/validate_tokens.py    Token validation utility
scripts/utils.py              Shared helpers (repo parsing, direction detection)
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Token errors | `python scripts/validate_tokens.py` |
| Modal errors | `modal token verify` |
| Repo not found | Check the repo ID on the source platform |
| Timeout on large repos | Specify `--repo-type` to skip auto-detect |
| ModelScope upload fails | Check `MODELSCOPE_TOKEN` write permissions |
| Unicode errors (Windows) | Prefix with `PYTHONIOENCODING=utf-8` |

## Benchmarks

Tested migrations (all cloud-to-cloud, no local disk):

| Scenario | Size | Duration |
|----------|------|----------|
| Single model HF→MS | 15.6 GB (67 files) | 7m 30s |
| Single model MS→HF | 163 MB | 18.2s |
| Single dataset HF→MS | 2.2 GB (7 files) | 14m 11s |
| Batch models (17 repos) | ~189 GB | 43m 44s |
| Batch datasets (largest) | 58.5 GB (16 files) | 19m 48s |

## License

[MIT](LICENSE)
