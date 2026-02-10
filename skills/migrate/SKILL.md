---
name: migrate
version: 1.4.0
description: >-
  This skill should be used when the user wants to migrate, transfer, push, copy,
  clone, or mirror repos between HuggingFace and ModelScope. Triggers on "migrate model",
  "transfer to ModelScope", "push to HuggingFace", "copy from HF to MS",
  "mirror model", "move dataset to ModelScope", "migrate space",
  "upload to ModelScope", "download from ModelScope",
  "sync repo between HuggingFace and ModelScope", "move this to modelscope",
  "put this on huggingface", "copy from ModelScope to HuggingFace",
  "batch migrate", "migrate multiple repos",
  "migrate all my models", "bulk transfer", "parallel migration",
  "migrate in background", "detached migration", or "fire and forget".
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

Each migration spins up a fresh Modal container, transfers files via platform SDKs, then destroys the container. Expect no persistent storage between runs.

## Supported Directions

- **HuggingFace -> ModelScope**: Download via `huggingface_hub.snapshot_download` (auto-falls back to `git clone` + `git lfs pull` if API returns 403). Upload via ModelScope `HubApi.upload_folder()`
- **ModelScope -> HuggingFace**: Download via `modelscope.hub.snapshot_download`, upload via `HfApi.upload_folder()`

## Migration Modes

### Standard (default)
Single container downloads the entire repo, then uploads it. Works for repos up to ~500 GB.

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

- **Auto-fallback to git clone**: If HuggingFace's API returns 403 (org storage limit lockout), the script automatically retries via `git clone` + `git lfs pull`. No manual `--use-git` needed.
- **Destination validation (fail-fast)**: Before downloading, the script validates the destination namespace exists on the target platform. Catches typos like `cagliostrolab-orgs` vs `cagliostrolab-org` before wasting time on a download.
- **Visibility preservation**: Private repos stay private on the destination. Source visibility is auto-detected and mapped (HF `private` → ModelScope visibility `1`=private / `5`=public).
- **Download progress monitoring**: For git-based downloads, a background thread monitors directory size and prints real-time progress (e.g., `Downloaded 42.3 GB (25.1 MB/s)`).
- **Post-migration verification**: After upload, file count and total size are compared between source and destination. A verification summary is printed.
- **Size estimation**: Before starting, the script estimates migration duration based on benchmark data and prints an ETA.
- **24-hour timeout**: All migration functions have an 86400s (24h) timeout, supporting repos up to ~3 TB in a single container.

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

Set tokens in the shell or place them in `${CLAUDE_PLUGIN_ROOT}/.env` — the validation script and `/migrate` command auto-load this file. Install `huggingface_hub` and `modelscope` locally for token validation. The migration itself runs entirely on Modal (no local installs needed for that).

## Executing a Migration

Use the `/migrate` command for the guided interactive workflow. It handles token validation, parameter extraction, user confirmation, and execution.

For direct CLI usage without the interactive workflow. Always source `.env`, set `PYTHONIOENCODING=utf-8` (prevents Modal CLI Unicode errors on Windows), and specify the `::main` or `::batch` entrypoint:

```bash
# Single repo (auto-detect type)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/my-model" --to ms

# Single repo (explicit type, custom destination)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/my-model" --to ms --repo-type model --dest "OrgName/model-v2"

# Parallel chunked (large repos — splits into chunks across up to 50 containers)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/big-dataset" --to ms --repo-type dataset --parallel

# Parallel with custom chunk size (default 20 GB, auto-adjusted for very large repos)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/huge-data" --to ms --repo-type dataset --parallel --chunk-size 50

# Batch (parallel containers, one per repo)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::batch" --source "user/model1,user/model2,user/model3" --to ms --repo-type model

# Detached (fire & forget — migration continues after session ends)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run --detach "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/my-model" --to ms
```

### Single vs Batch

| Aspect | Single | Batch |
|--------|--------|-------|
| Entrypoint | `modal_migrate.py::main` | `modal_migrate.py::batch` |
| `--source` | One repo ID | Comma-separated list |
| `--repo-type` | Optional (auto-detects) | Optional (default: `model`) |
| `--dest` | Supported | **NOT supported** |
| Type detection | Per-repo auto-detect | Applied uniformly |
| Parallelism | One container | One container per repo via `starmap()` |
| Existing repos | Warns, proceeds | Auto-skips |

**Important**: Batch mode uses the source repo ID as the destination, so it only works when source and destination namespaces match. If the HF org name differs from the MS org name (e.g., `cagliostrolab` on HF vs `cagliostrolab-org` on MS), use individual `::main` runs with `--dest` instead of batch.

### Detached Mode (Fire & Forget)

Add `--detach` before the script path to run in fire-and-forget mode. The migration continues in Modal's cloud after the session ends. Monitor with `modal app logs hf-ms-migrate` (app name defined in `modal_migrate.py`) or the [Modal dashboard](https://modal.com/apps). Both single and batch entrypoints support detach.

The `/migrate` command infers direction from natural language (e.g., "to ModelScope" becomes `--to ms`) and extracts repo IDs from URLs automatically. Pass bare `namespace/name` format to the script, not full URLs.

## Edge Cases

- **Repo already exists on destination**: Single mode proceeds with a warning (files are updated/overwritten). Batch mode auto-skips existing repos.
- **Private source repo**: Works if the source token has read access. Visibility is preserved — private repos are created as private on the destination.
- **403 Forbidden (storage limit lockout)**: Auto-detected and handled. The script falls back to `git clone` + `git lfs pull`, which bypasses HuggingFace's API lockout on orgs exceeding private storage limits.
- **Spaces to ModelScope**: Skipped with a warning. ModelScope Studios are web/git only — the SDK has no support. To force migration as a model repo, use `--repo-type model`.
- **Large repos (>50GB)**: Use `--parallel` to split across up to 100 containers. The Modal function timeout is 86400s (24 hours). Tested up to 58.5 GB single-container and 3.3 TB parallel (113 chunks).
- **Very large repos (>1TB)**: Use `--parallel`. Chunk size auto-adjusts to keep within 100 containers (e.g., 3.3 TB -> ~30 GB chunks -> 113 containers). Consider `--chunk-size 50` or higher to reduce git clone overhead.
- **Repos with millions of files**: Each parallel chunk re-clones the full tree structure. For repos with >500K files, the script warns and suggests larger chunk sizes to reduce this overhead.
- **ModelScope namespace**: Defaults to same as source. Destination namespace must already exist on ModelScope or match the authenticated user.
- **ModelScope private repo listing**: ModelScope's API does not list private repos via `list_models`/`list_datasets`. To check if a private repo exists, use `api.repo_exists(repo_id=..., repo_type=..., token=...)` per-repo instead.
- **Batch with mismatched namespaces**: Batch mode does not support `--dest`. If source org ≠ dest org (e.g., `cagliostrolab` on HF vs `cagliostrolab-org` on MS), batch will fail with "Unauthorized to create". Use individual `::main` runs with `--dest`.

## Troubleshooting

| Error Pattern | Suggestion |
|---------------|------------|
| "not set" or "token" | Re-check tokens: `python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py"` |
| "not found" or "404" | Verify the repo ID exists on the source platform |
| "403" or "Forbidden" | Usually auto-handled (falls back to git clone). If persistent, check token permissions |
| "timeout" | Repo may be very large; use `--parallel` for large repos, or specify `--repo-type` to skip auto-detect |
| "Modal" or "container" | Check Modal account: `modal token verify` |
| "upload" errors | ModelScope upload issue; check MODELSCOPE_TOKEN permissions |
| "Unauthorized to 创建" | ModelScope namespace mismatch — batch mode used source namespace. Use `::main` with `--dest` instead |
| Network/connection errors | Transient; retry the migration |

## Post-Migration Verification

After migration, you can verify data integrity by comparing SHA256 hashes across platforms. This is the gold standard before deleting the source repo.

**How it works:**
- HuggingFace: `hf_api.dataset_info(repo_id, files_metadata=True)` → each sibling's `.lfs.sha256`
- ModelScope: `api.get_dataset_files(repo_id, recursive=True)` → each file's `Sha256` field (paginate with `page_number`/`page_size`)
- Compare per-file. Skip platform-generated files (`.gitattributes`, `README.md`) as these differ between platforms.

**Gotchas:**
- Re-packed tars with different filenames will have different SHA256 even if the underlying images are identical. SHA256 verification only works for byte-identical files.
- ModelScope `get_dataset_files` returns max 100 per page — always paginate for large repos.
- HuggingFace `files_metadata=True` may undercount files in repos with deeply nested directories.

## Org Cleanup Workflow

For cleaning up an org with many repos (migrating to ModelScope, then deleting from HF), use the Socratic approach:

1. **Inventory**: List all repos with size, type, visibility, creation date, last update
2. **Check backup status**: For each repo, check if it exists on ModelScope via `repo_exists()`
3. **Verify before delete**: Run SHA256 cross-platform verification on backed-up repos
4. **Present one-by-one**: Show the user each repo with full context and let them decide migrate/delete/keep
5. **Execute**: Migrate in background (parallel for large repos), delete after verification

**Key patterns from real cleanup sessions:**
- Repos named "poc", "test", "v0", or "half_done" are usually safe to delete
- Datasets that are subsets of larger datasets (e.g., 70k subset of 2.6M) are redundant once the parent is backed up
- Re-packed datasets under different orgs (same images, different tar names) are duplicates even though SHA256 won't match — compare directory structure and file counts instead
- Public model repos (like released checkpoints) should typically stay on HF for community access
- Private training datasets are the best candidates for migrate-then-delete

## Scripts Reference

| Script | Purpose | Run As |
|--------|---------|--------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py` | Modal app with migration functions | `modal run ...` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py` | Check all platform tokens | `python ...` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/utils.py` | Shared helpers (imported by other scripts) | N/A (library) |

## SDK Reference

Before searching the web for HuggingFace or ModelScope SDK methods, read the bundled reference file. It contains complete Python SDK signatures for both platforms (list, info, create, download, upload, files, branches) and a key differences table.

**Read first**: `${CLAUDE_PLUGIN_ROOT}/skills/migrate/references/hub-api-reference.md`
