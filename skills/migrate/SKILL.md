---
name: migrate
version: 1.2.0
description: >-
  This skill should be used when the user wants to migrate, transfer, push, copy,
  or mirror repos between HuggingFace and ModelScope. Triggers on "migrate model",
  "transfer to ModelScope", "push to HuggingFace", "copy from HF to MS",
  "mirror model", "move dataset to ModelScope", "migrate space",
  "upload to ModelScope", "download from ModelScope",
  "sync repo between HuggingFace and ModelScope", "move this to modelscope",
  "put this on huggingface", "copy from ModelScope to HuggingFace",
  "batch migrate", "migrate multiple repos",
  "migrate all my models", "bulk transfer", or "parallel migration".
---

# HF-Modal-ModelScope Migration

Migrate repos between HuggingFace and ModelScope using Modal as an ephemeral cloud compute bridge. No files touch the local machine — everything transfers cloud-to-cloud.

## Architecture

```
Local Machine              Modal Container              Platforms
┌──────────┐  modal run  ┌─────────────────┐  API     ┌──────────┐
│ Claude   │ ──────────> │ snapshot_download│ <──────> │ HF Hub   │
│ Code     │             │ upload_folder    │ <──────> │ MS Hub   │
└──────────┘             └─────────────────┘          └──────────┘
```

Modal containers are ephemeral — each migration spins up a fresh container, transfers files via platform SDKs, then the container is destroyed. No persistent storage.

## Supported Directions

- **HuggingFace -> ModelScope**: Download via `huggingface_hub.snapshot_download`, upload via ModelScope `HubApi.upload_folder()`
- **ModelScope -> HuggingFace**: Download via `modelscope.hub.snapshot_download`, upload via `HfApi.upload_folder()`

## Supported Repo Types

| Type | HF -> MS | MS -> HF | Notes |
|------|----------|----------|-------|
| Models | Yes | Yes | Weights, configs, tokenizers |
| Datasets | Yes | Yes | Data files, metadata |
| Spaces | Partial (as model repo) | N/A | ModelScope has no Spaces equivalent |

## Prerequisites

Three sets of credentials must be available as environment variables:

| Variable | Platform | How to Get |
|----------|----------|------------|
| `HF_TOKEN` | HuggingFace | https://huggingface.co/settings/tokens (needs read + write) |
| `MODAL_TOKEN_ID` | Modal | Run `modal token new` or https://modal.com/settings |
| `MODAL_TOKEN_SECRET` | Modal | Same as above |
| `MODELSCOPE_TOKEN` | ModelScope | https://modelscope.ai/my/myaccesstoken |
| `MODELSCOPE_DOMAIN` | ModelScope (optional) | Defaults to `modelscope.cn`. Set to `modelscope.ai` for international site. |

Tokens can be set in the shell or placed in `${CLAUDE_PLUGIN_ROOT}/.env` — the validation script and `/migrate` command auto-load this file. Ensure `huggingface_hub` and `modelscope` are pip-installed locally for token validation. The migration itself runs entirely on Modal (no local installs needed for that).

## Executing a Migration

Use the `/migrate` command for the guided interactive workflow. It handles token validation, parameter extraction, user confirmation, and execution.

For direct CLI usage without the interactive workflow. Always source `.env`, set `PYTHONIOENCODING=utf-8` (prevents Modal CLI Unicode errors on Windows), and specify the `::main` or `::batch` entrypoint:

```bash
# Single repo (auto-detect type)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/my-model" --to ms

# Single repo (explicit type, custom destination)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "username/my-model" --to ms --repo-type model --dest "OrgName/model-v2"

# Batch (parallel containers, one per repo)
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::batch" --source "user/model1,user/model2,user/model3" --to ms --repo-type model
```

### Single vs Batch

| Aspect | Single | Batch |
|--------|--------|-------|
| Entrypoint | `modal_migrate.py::main` | `modal_migrate.py::batch` |
| `--source` | One repo ID | Comma-separated list |
| `--repo-type` | Optional (auto-detects) | Optional (default: `model`) |
| Type detection | Per-repo auto-detect | Applied uniformly |
| Parallelism | One container | One container per repo via `starmap()` |
| Existing repos | Warns, proceeds | Auto-skips |

The `/migrate` command handles direction inference from natural language (e.g., "to ModelScope" -> `--to ms`) and URL-to-repo-ID extraction automatically. The script expects bare `namespace/name` format, not full URLs.

## Edge Cases

- **Repo already exists on destination**: Single mode proceeds with a warning (files are updated/overwritten). Batch mode auto-skips existing repos.
- **Private source repo**: Works if the source token has read access.
- **Spaces to ModelScope**: Space files are uploaded as a model repo (ModelScope has no Spaces equivalent).
- **Large repos (>10GB)**: The Modal function has a 3600s (1 hour) timeout. Tested up to 58.5 GB successfully.
- **ModelScope namespace**: Defaults to same as source. Destination namespace must already exist on ModelScope or match the authenticated user.

## Troubleshooting

| Error Pattern | Suggestion |
|---------------|------------|
| "not set" or "token" | Re-check tokens: `python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py"` |
| "not found" or "404" | Verify the repo ID exists on the source platform |
| "timeout" | Repo may be very large; try again or specify `--repo-type` to skip auto-detect |
| "Modal" or "container" | Check Modal account: `modal token verify` |
| "upload" errors | ModelScope upload issue; check MODELSCOPE_TOKEN permissions |
| Network/connection errors | Transient; retry the migration |

## Scripts Reference

| Script | Purpose | Run As |
|--------|---------|--------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py` | Modal app with migration functions | `modal run ...` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py` | Check all platform tokens | `python ...` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/utils.py` | Shared helpers (imported by other scripts) | N/A (library) |

## SDK Reference

Before searching the web for HuggingFace or ModelScope SDK methods, read the bundled reference file. It contains complete Python SDK signatures for both platforms (list, info, create, download, upload, files, branches) and a key differences table.

**Read first**: `${CLAUDE_PLUGIN_ROOT}/skills/migrate/references/hub-api-reference.md`
