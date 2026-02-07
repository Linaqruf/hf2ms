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
- Out of scope: batch migration, format conversion, quantization, scheduling

## Commands

- `modal run scripts/modal_migrate.py --source <repo> --to <hf|ms>` — Run migration
- `modal run scripts/modal_migrate.py::hello_world` — Smoke test Modal setup
- `python scripts/validate_tokens.py` — Validate all platform tokens
- `/migrate` — Claude Code slash command for guided migration

## Current Status

All phases complete. Tested end-to-end: hitokomoru-diffusion-v2 (67 files, 15.6 GB) migrated in 7m30s.
