# HF-Modal-ModelScope Migration Plugin

Migrate HuggingFace repos (models, datasets, spaces) to/from ModelScope using Modal as cloud compute. No local downloads.

## Spec Reference

Primary spec: `SPEC.md`

## Key Constraints

- All file transfers happen on Modal containers — never download to local machine
- Minimal container image: only `huggingface_hub` + `modelscope` SDKs, no torch/transformers
- Ephemeral containers only (no persistent Modal Volumes for v1)
- Three tokens required: `HF_TOKEN`, `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`, `MODELSCOPE_TOKEN`
- ModelScope `push_model()` requires `configuration.json` in model dir (auto-generated if missing)
- Out of scope: batch migration, format conversion, quantization, scheduling

## Commands

- `modal run scripts/modal_migrate.py --source <repo> --to <hf|ms>` — Run migration
- `modal run scripts/modal_migrate.py::hello_world` — Smoke test Modal setup
- `python scripts/validate_tokens.py` — Validate all platform tokens
- `/migrate` — Claude Code slash command for guided migration

## Current Status

Phases 1-4 complete (code-complete). Manual testing with live credentials pending.
→ Check `SPEC.md` → Development Phases for test checklist
→ Start new dev sessions with `prompt.md`
