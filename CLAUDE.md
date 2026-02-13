# hf2ms — Cloud-to-Cloud ML Repo Migration

Migrate HuggingFace repos (models, datasets) to/from ModelScope using Modal as a cloud compute bridge. No files touch the local machine.

GitHub: https://github.com/Linaqruf/hf2ms

## Key Constraints

- All file transfers happen on Modal containers — never download to local machine
- Minimal container image: `huggingface_hub` + `modelscope` + `git-lfs` (no torch/transformers)
- Ephemeral containers only (no persistent Modal Volumes)
- Tokens: `HF_TOKEN`, `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`, `MODELSCOPE_TOKEN`
- Optional: `MODELSCOPE_DOMAIN` (default: `modelscope.cn`, set `modelscope.ai` for international)
- Modal only auto-mounts the entrypoint file — `utils.py` imports must be lazy (inside `main()`)
- 24-hour timeout per container (86400s)
- Parallel mode: up to 100 concurrent containers per migration

## Commands

```bash
# Single repo
modal run scripts/modal_migrate.py::main --source "user/repo" --to ms

# Parallel (large repos)
modal run scripts/modal_migrate.py::main --source "user/repo" --to ms --parallel

# Batch (multiple repos)
modal run scripts/modal_migrate.py::batch --source "repo1,repo2,repo3" --to ms --repo-type model

# Detached (fire & forget)
modal run --detach scripts/modal_migrate.py::main --source "user/repo" --to ms

# Token validation
python scripts/validate_tokens.py

# Smoke test
modal run scripts/modal_migrate.py::hello_world
```

On Windows: prefix with `PYTHONIOENCODING=utf-8` to avoid Modal CLI Unicode errors.

## Setup

```bash
pip install -r requirements.txt
python scripts/validate_tokens.py
```

## Architecture

- `scripts/modal_migrate.py` — Modal app with all remote functions
- `scripts/validate_tokens.py` — Token validation utility
- `scripts/utils.py` — Shared helpers (repo parsing, direction detection)
- `commands/migrate.md` — `/migrate` slash command for Claude Code
- `skills/migrate/SKILL.md` — Natural language migration skill
- `skills/migrate/references/hub-api-reference.md` — SDK reference
- `skills/migrate/references/verification-and-cleanup.md` — Post-migration verification & cleanup guide

## Built-in Safety

- **Auto-fallback**: `snapshot_download()` fails with 403? Automatically retries via `git clone` + `git lfs pull`
- **Fail-fast validation**: Destination namespace checked before download starts
- **Visibility preservation**: Private repos stay private on destination
- **SHA256 verification**: LFS file hashes checked after upload (skips platform-generated files and files without extractable hashes)
- **Progress monitoring**: Real-time directory size tracking during git downloads
- **Size estimation**: ETA printed before migration starts

## Current Status

Version 1.4.0 — all core features shipped. v1.5.0 planned: developer onboarding, dry-run mode, selective migration.

-> Development plan in SPEC.md (Phases 9-11)
