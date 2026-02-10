---
name: migrate
version: 1.4.0
description: >-
  This skill should be used when the user wants to migrate, transfer, push, copy,
  or mirror repos between HuggingFace and ModelScope. Triggers on "migrate model",
  "transfer to ModelScope", "push to HuggingFace", "copy from HF to MS",
  "mirror model", "move dataset to ModelScope", "migrate space",
  "upload to ModelScope", "download from ModelScope",
  "sync repo between HuggingFace and ModelScope", "move this to modelscope",
  "put this on huggingface", "copy from ModelScope to HuggingFace",
  "batch migrate", "migrate multiple repos",
  "migrate all my models", "bulk transfer", "parallel migration",
  "migrate in background", "detached migration", "fire and forget",
  "verify migration", "check migration integrity",
  "clean up org after migration", or "inventory repos for migration".
---

# HF-Modal-ModelScope Migration

Migrate repos between HuggingFace and ModelScope using Modal as an ephemeral cloud compute bridge. No files touch the local machine — everything transfers cloud-to-cloud.

## Architecture

```
Local Machine              Modal Container              Platforms
┌──────────┐  modal run  ┌─────────────────┐  API     ┌──────────┐
│ Claude   │ ──────────> │ snapshot_download│ <──────> │ HF Hub   │
│ Code     │             │   OR git clone   │          │          │
│          │             │ upload_folder    │ <──────> │ MS Hub   │
└──────────┘             └─────────────────┘          └──────────┘
```

Each migration spins up a fresh Modal container, transfers files via platform SDKs, then destroys the container. No persistent storage between runs.

## Supported Directions

- **HuggingFace -> ModelScope**: Download via `huggingface_hub.snapshot_download` (auto-falls back to `git clone` + `git lfs pull` if API returns 403). Upload via ModelScope `HubApi.upload_folder()`
- **ModelScope -> HuggingFace**: Download via `modelscope.hub.snapshot_download`, upload via `HfApi.upload_folder()`

## Migration Modes

### Standard (default)
Single container downloads the entire repo, then uploads it. Tested up to 58.5 GB; 24-hour timeout supports larger repos. Use `--parallel` for anything over ~50 GB.

### Parallel (`--parallel`)
Splits the repo into chunks, each processed by an independent container. Up to 100 containers run concurrently. Each chunk worker: clones repo structure (no LFS data) -> selectively pulls its assigned LFS files -> uploads to destination. Best for repos over 50 GB.

```
                    ┌─ Container 0: clone + pull chunk 0 + upload ─┐
HuggingFace ────────┼─ Container 1: clone + pull chunk 1 + upload ─┼──── ModelScope
  (source)          ├─ Container 2: clone + pull chunk 2 + upload ─┤    (dest)
                    └─ ...up to 100 concurrent containers...       ┘
```

**Guardrails:**
- Chunk count auto-capped at 100 (chunk size increased automatically for very large repos)
- Repos with >500K files get a warning (git clone overhead per chunk)
- Post-upload verification compares file count and size against source manifest

## Built-in Safety & Reliability

These features run automatically — no flags needed:

- **Auto-fallback to git clone**: If HuggingFace's API returns 403 (org storage limit lockout), the script automatically retries via `git clone` + `git lfs pull`.
- **Destination validation (fail-fast)**: Before downloading, validates the destination namespace exists. Catches typos before wasting time on a download.
- **Visibility preservation**: Private repos stay private on the destination. Source visibility is auto-detected and mapped.
- **SHA256 verification**: Every LFS file is hash-checked after upload. Skipped files (no extractable hash) are reported separately.
- **Download progress monitoring**: For git-based downloads, a background thread monitors directory size and prints real-time progress.
- **Size estimation**: Before starting, estimates migration duration based on benchmark data and prints an ETA.
- **24-hour timeout**: All migration functions have an 86400s (24h) timeout. Tested up to 58.5 GB single-container; use `--parallel` for larger repos.

## Supported Repo Types

| Type | HF -> MS | MS -> HF | Notes |
|------|----------|----------|-------|
| Models | Yes | Yes | Weights, configs, tokenizers |
| Datasets | Yes | Yes | Data files, metadata |
| Spaces | Skipped (warning) | N/A | ModelScope Studios are web/git only — SDK has no support |

## Prerequisites

Three sets of credentials must be available as environment variables:

| Variable | Platform | How to Get |
|----------|----------|------------|
| `HF_TOKEN` | HuggingFace | https://huggingface.co/settings/tokens (needs read + write) |
| `MODAL_TOKEN_ID` | Modal | Run `modal token new` or https://modal.com/settings |
| `MODAL_TOKEN_SECRET` | Modal | Same as above |
| `MODELSCOPE_TOKEN` | ModelScope | https://modelscope.ai/my/myaccesstoken |
| `MODELSCOPE_DOMAIN` | ModelScope (optional) | Defaults to `modelscope.cn`. Set to `modelscope.ai` for international site. |

Tokens can be set in the shell or placed in `${CLAUDE_PLUGIN_ROOT}/.env` — the validation script and `/migrate` command auto-load this file. Install `huggingface_hub` and `modelscope` locally for token validation. The migration itself runs entirely on Modal.

## Executing a Migration

Use the `/migrate` command for the guided interactive workflow. It handles token validation, parameter extraction, user confirmation, and execution.

For direct CLI usage, all commands below use this prefix (sources `.env` and prevents Unicode errors on Windows):

```bash
PREFIX="set -a && source \"${CLAUDE_PLUGIN_ROOT}/.env\" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8"
```

**Single repo:**
```bash
eval $PREFIX modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/my-model" --to ms
```

**Parallel chunked (large repos):**
```bash
eval $PREFIX modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "org/big-dataset" --to ms --repo-type dataset --parallel --chunk-size 30
```

**Batch (one container per repo):**
```bash
eval $PREFIX modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::batch" --source "user/model1,user/model2" --to ms --repo-type model
```

**Detached (fire & forget):**
```bash
eval $PREFIX modal run --detach "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/my-model" --to ms
```

### Key Differences: Single vs Batch

| Aspect | Single (`::main`) | Batch (`::batch`) |
|--------|-------------------|-------------------|
| `--source` | One repo ID | Comma-separated list |
| `--dest` | Supported | **NOT supported** |
| Existing repos | Warns, proceeds | Auto-skips |

Batch mode uses the source repo ID as the destination. If the HF org name differs from the MS org name, use individual `::main` runs with `--dest`.

### Detached Mode (Fire & Forget)

Add `--detach` before the script path. The migration continues in Modal's cloud after the session ends. Monitor with `modal app logs hf-ms-migrate` or the [Modal dashboard](https://modal.com/apps).

## Edge Cases

- **Repo already exists on destination**: Single mode proceeds with a warning (files are updated/overwritten). Batch mode auto-skips existing repos.
- **Private source repo**: Works if the source token has read access. Visibility is preserved.
- **403 Forbidden (storage limit lockout)**: Auto-detected. Falls back to `git clone` + `git lfs pull`.
- **Spaces to ModelScope**: Skipped with a warning. To force migration as a model repo, use `--repo-type model`.
- **Large repos (>50GB)**: Use `--parallel`. Tested up to 58.5 GB single-container and 3.3 TB parallel (113 chunks).
- **Very large repos (>1TB)**: Use `--parallel`. Chunk size auto-adjusts to keep within 100 chunks (e.g., 3.3 TB -> ~30 GB chunks -> 113 chunks, processed in waves of up to 100 concurrent containers).
- **Batch with mismatched namespaces**: Batch mode does not support `--dest`. If source org differs from dest org, use individual `::main` runs with `--dest`.

## Troubleshooting

| Error Pattern | Suggestion |
|---------------|------------|
| "not set" or "token" | Re-check tokens: `python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py"` |
| "not found" or "404" | Verify the repo ID exists on the source platform |
| "403" or "Forbidden" | Usually auto-handled (falls back to git clone). If persistent, check token permissions |
| "timeout" | Use `--parallel` for large repos, or `--repo-type` to skip auto-detect |
| "Modal" or "container" | Check Modal account: `modal token verify` |
| "upload" errors | Check MODELSCOPE_TOKEN permissions |
| "Unauthorized" | Namespace mismatch — use `::main` with `--dest` instead of batch |

## Verification & Org Cleanup

For SHA256 cross-platform verification and org cleanup workflows (inventory, verify, migrate-then-delete), read: `${CLAUDE_PLUGIN_ROOT}/skills/migrate/references/verification-and-cleanup.md`

## Scripts Reference

| Script | Purpose | Run As |
|--------|---------|--------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py` | Modal app with migration functions | `modal run ...` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py` | Check all platform tokens | `python ...` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/utils.py` | Shared helpers (imported by other scripts) | N/A (library) |

## SDK Reference

Before searching the web for HuggingFace or ModelScope SDK methods, read the bundled reference file. It contains complete Python SDK signatures for both platforms (list, info, create, download, upload, files, branches) and a key differences table.

**Read first**: `${CLAUDE_PLUGIN_ROOT}/skills/migrate/references/hub-api-reference.md`
