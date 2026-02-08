# HF2MS

Claude Code plugin that migrates repos between HuggingFace and ModelScope using [Modal](https://modal.com) as cloud compute. No files touch your local machine.

> Tested: 17 models (~189 GB) in 43m44s, 3 datasets (~63 GB) — all batch-migrated with parallel containers

## How It Works

```
Your Machine                    Modal Container                    Platforms
┌──────────┐    modal run    ┌─────────────────┐    API calls    ┌──────────┐
│ Claude   │ ──────────────> │ snapshot_download│ <────────────> │ HF Hub   │
│ Code     │                 │ upload_folder    │ <────────────> │ MS Hub   │
└──────────┘                 └─────────────────┘                 └──────────┘
```

1. You say "migrate this model to ModelScope"
2. The plugin spins up a Modal container in the cloud
3. The container downloads from the source platform
4. The container uploads to the destination platform
5. Done — no local disk space used

## Prerequisites

- **Python 3.11+**
- **Modal CLI** — `pip install modal` then `modal token new`
- **Platform tokens** (see Setup below)

## Setup

### 1. Install Modal

```bash
pip install modal
modal token new
```

### 2. Set Environment Variables

Copy the template and fill in your tokens:

```bash
cp .env.example .env
```

| Variable | Where to Get It |
|----------|----------------|
| `HF_TOKEN` | https://huggingface.co/settings/tokens (needs read + write) |
| `MODAL_TOKEN_ID` | `modal token new` or https://modal.com/settings |
| `MODAL_TOKEN_SECRET` | Same as above |
| `MODELSCOPE_TOKEN` | https://modelscope.ai/my/myaccesstoken |
| `MODELSCOPE_DOMAIN` | Optional. Set to `modelscope.ai` for international site (default: `modelscope.cn`) |

Load them into your shell:

```bash
# bash/zsh
export $(cat .env | xargs)

# PowerShell
Get-Content .env | ForEach-Object { if ($_ -match '^([^#].+?)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }
```

### 3. Validate Tokens

```bash
python scripts/validate_tokens.py
```

### 4. Test Modal Connection

```bash
modal run scripts/modal_migrate.py::hello_world
```

## Usage

### Via Claude Code (Plugin)

Install this plugin in Claude Code, then:

```
> migrate Linaqruf/animagine-xl-3.1 to ModelScope
> transfer damo/text-to-video to HuggingFace
> /migrate Linaqruf/my-dataset --to ms --type dataset
```

The plugin will validate tokens, confirm the migration, and run it.

### Via Modal CLI (Single Repo)

```bash
# HuggingFace → ModelScope (auto-detect type)
modal run scripts/modal_migrate.py::main --source "Linaqruf/animagine-xl-3.1" --to ms

# ModelScope → HuggingFace (explicit type)
modal run scripts/modal_migrate.py::main --source "damo/text-to-video" --to hf --repo-type model

# Custom destination name
modal run scripts/modal_migrate.py::main --source "Linaqruf/model" --to ms --dest "MyOrg/model-v2"

# Using platform prefix
modal run scripts/modal_migrate.py::main --source "hf:Linaqruf/model" --to ms
```

> **Windows note**: Prefix commands with `PYTHONIOENCODING=utf-8` to avoid Unicode errors from Modal CLI output.

### Via Modal CLI (Batch — Parallel Containers)

Migrate multiple repos simultaneously, each in its own Modal container:

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

Each repo gets its own container and runs in parallel via Modal's `starmap()`. Repos that already exist on the destination are automatically skipped.

### Options (Single)

| Flag | Description | Required |
|------|-------------|----------|
| `--source` | Source repo ID (e.g., `user/model` or `hf:user/model`) | Yes |
| `--to` | Destination platform: `hf` or `ms` | Yes* |
| `--repo-type` | `model`, `dataset`, or `space` (auto-detects if omitted) | No |
| `--dest` | Custom destination repo ID (defaults to same as source) | No |

*Not required if source has a platform prefix (`hf:` or `ms:`).

### Options (Batch)

| Flag | Description | Required |
|------|-------------|----------|
| `--source` | Comma-separated repo IDs | Yes |
| `--to` | Destination platform: `hf` or `ms` | Yes |
| `--repo-type` | `model`, `dataset`, or `space` (applied to all repos) | No (default: model) |

## Supported Repo Types

| Type | HF → MS | MS → HF |
|------|---------|---------|
| Models | Yes | Yes |
| Datasets | Yes | Yes |
| Spaces | Yes (files only) | N/A |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Token errors | Run `python scripts/validate_tokens.py` |
| Modal errors | Run `modal token verify` |
| Repo not found | Check the repo ID on the source platform |
| Timeout on large repos | Try with `--repo-type` to skip auto-detect |
| ModelScope upload fails | Ensure `MODELSCOPE_TOKEN` has write permissions |

## Project Structure

```
.claude-plugin/plugin.json    Plugin manifest
commands/migrate.md           /migrate slash command
skills/migrate/SKILL.md       Natural language migration skill
scripts/modal_migrate.py      Modal app (migration functions)
scripts/validate_tokens.py    Token validation utility
scripts/utils.py              Shared helpers
```
