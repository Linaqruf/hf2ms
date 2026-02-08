# HF2MS — HuggingFace to ModelScope Migration Plugin

Migrate HuggingFace repos (models, datasets, spaces) to/from ModelScope using Modal as cloud compute. No local downloads.

GitHub: https://github.com/Linaqruf/hf2ms

## Spec Reference

Primary spec: `SPEC.md`

## Key Constraints

- All file transfers happen on Modal containers — never download to local machine
- Minimal container image: only `huggingface_hub` + `modelscope` SDKs, no torch/transformers
- Ephemeral containers only (no persistent Modal Volumes for v1)
- Tokens required: `HF_TOKEN`, `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`, `MODELSCOPE_TOKEN`
- Optional: `MODELSCOPE_DOMAIN` — set to `modelscope.ai` for international site (default: `modelscope.cn`)
- ModelScope upload uses `HubApi.upload_folder()` (HTTP-based, no git required)
- Modal only auto-mounts the entrypoint file — `utils.py` imports must be lazy (inside `main()`)
- Batch migration uses `starmap()` for parallel containers — each repo gets its own container
- Destination existence check: single mode warns, batch mode auto-skips existing repos
- Out of scope: format conversion, quantization, scheduling

## Commands

- `modal run scripts/modal_migrate.py::main --source <repo> --to <hf|ms>` — Single migration
- `modal run scripts/modal_migrate.py::batch --source "repo1,repo2,repo3" --to <hf|ms> --repo-type <type>` — Batch (parallel)
- `modal run --detach scripts/modal_migrate.py::main --source <repo> --to <hf|ms>` — Detached single (fire & forget)
- `modal run --detach scripts/modal_migrate.py::batch --source "repo1,repo2" --to <hf|ms> --repo-type <type>` — Detached batch
- `modal run scripts/modal_migrate.py::hello_world` — Smoke test Modal setup
- On Windows: prefix with `PYTHONIOENCODING=utf-8` to avoid Modal CLI Unicode errors
- `python scripts/validate_tokens.py` — Validate all platform tokens
- `/migrate` — Claude Code slash command for guided migration
- `modal app logs hf-ms-migrate` / `modal app list` / `modal app stop hf-ms-migrate` — Monitor detached runs

## Current Status

All phases complete. Tested: single migration (15.6 GB, 7m30s), batch models (17 repos, ~189 GB, 43m44s), batch datasets (3 repos, ~63 GB).
