---
name: migrate
version: 1.0.0
description: >-
  This skill should be used when the user wants to migrate, transfer, push, copy,
  or mirror repos between HuggingFace and ModelScope. Triggers on "migrate model",
  "transfer to ModelScope", "push to HuggingFace", "copy from HF to MS",
  "mirror model", "move dataset to ModelScope", "upload to ModelScope",
  "sync repo between HuggingFace and ModelScope", "move this to modelscope",
  or "put this on huggingface".
---

# HF-Modal-ModelScope Migration

Migrate repos between HuggingFace and ModelScope using Modal as an ephemeral cloud compute bridge. No files touch the local machine — everything transfers cloud-to-cloud.

## Supported Directions

- **HuggingFace -> ModelScope**: Download on Modal, upload to ModelScope
- **ModelScope -> HuggingFace**: Download on Modal, upload to HuggingFace

## Supported Repo Types

- **Models** — weights, configs, tokenizers
- **Datasets** — data files, metadata
- **Spaces** — files only (no deployment on ModelScope)

## Prerequisites

Three sets of credentials must be available as environment variables:

| Variable | Platform | How to Get |
|----------|----------|------------|
| `HF_TOKEN` | HuggingFace | https://huggingface.co/settings/tokens (needs read + write) |
| `MODAL_TOKEN_ID` | Modal | Run `modal token new` or https://modal.com/settings |
| `MODAL_TOKEN_SECRET` | Modal | Same as above |
| `MODELSCOPE_TOKEN` | ModelScope | https://modelscope.ai/my/myaccesstoken |
| `MODELSCOPE_DOMAIN` | ModelScope (optional) | Defaults to `modelscope.cn`. Set to `modelscope.ai` for international site. |

For token validation to work locally, `huggingface_hub` and `modelscope` must be pip-installed on the local machine. The migration itself runs entirely on Modal (no local installs needed for that).

## Workflow

When the user requests a migration, follow these steps **in order**. Do not skip steps.

### Step 1: Validate Tokens

Run the validation script:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py"
```

**If tokens are missing or invalid:**
- Show the user exactly which tokens failed
- Provide the URLs to obtain them (from the table above)
- STOP — do not proceed until tokens are fixed

**If all tokens pass:**
- Briefly confirm: "All tokens validated."
- Continue to next step.

### Step 2: Extract Migration Parameters

From the user's message, extract:

1. **Source repo ID** — e.g., `Linaqruf/animagine-xl-3.1`, `damo/text-to-video`
2. **Direction** — which platform is the source, which is the destination
3. **Repo type** — model, dataset, or space (optional, auto-detects if not stated)
4. **Custom destination** — if user wants a different name on the destination (optional)

**Extraction rules:**
- If the user says "migrate X to ModelScope" -> source is HF, dest is MS
- If the user says "migrate X to HuggingFace" / "migrate X to HF" -> source is MS, dest is HF
- If the user says "migrate X from ModelScope" -> source is MS, dest is HF
- If the user prefixes with `hf:` or `ms:` -> platform is explicit
- If direction is ambiguous, ask:

```typescript
{
  question: "Which direction should the migration go?",
  header: "Direction",
  options: [
    { label: "HuggingFace to ModelScope", description: "Download from HF, upload to ModelScope" },
    { label: "ModelScope to HuggingFace", description: "Download from ModelScope, upload to HF" }
  ]
}
```

- If no repo ID is found in the message, ask:
  "What's the repo ID you'd like to migrate? (e.g., `Linaqruf/animagine-xl-3.1`)"

### Step 3: Confirm with User

Always confirm before running. Present a summary:

```
Migration Summary:
  Source:      [HuggingFace|ModelScope] / [repo-id]
  Destination: [HuggingFace|ModelScope] / [dest-repo-id]
  Type:        [model|dataset|space|auto-detect]
```

Then ask:

```typescript
{
  question: "Ready to start this migration?",
  header: "Confirm",
  options: [
    { label: "Yes, migrate", description: "Start the cloud-to-cloud migration via Modal" },
    { label: "Change something", description: "Modify repo, direction, or destination name" },
    { label: "Cancel", description: "Abort" }
  ]
}
```

- If "Change something" -> ask what to change, update parameters, re-confirm
- If "Cancel" -> stop

### Step 4: Execute Migration

Build and run the Modal command:

```bash
modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py" --source "<repo-id>" --to <hf|ms>
```

**Optional flags** (add only if applicable):
- `--repo-type <model|dataset|space>` — only if user specified type
- `--dest "<namespace/name>"` — only if user wants a different destination name

**Examples:**

```bash
# Basic: auto-detect type, same name on destination
modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py" --source "Linaqruf/animagine-xl-3.1" --to ms

# With explicit type
modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py" --source "Linaqruf/animagine-xl-3.1" --to ms --repo-type model

# With custom destination
modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py" --source "Linaqruf/model" --to ms --dest "MyOrg/model-v2"

# Reverse: ModelScope to HuggingFace
modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py" --source "damo/text-to-video" --to hf
```

### Step 5: Report Result

**On success** (exit code 0, output contains "Migration complete"):
- Report the destination URL
- Report the file count
- Example: "Migration complete! 42 files transferred to https://modelscope.cn/models/Linaqruf/animagine-xl-3.1"

**On failure** (non-zero exit code or error in output):
- Show the error message from the output
- Suggest troubleshooting based on the error:

| Error Pattern | Suggestion |
|---------------|------------|
| "not set" or "token" | Re-check tokens: `python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py"` |
| "not found" or "404" | Verify the repo ID exists on the source platform |
| "timeout" | Repo may be very large; try again or specify `--repo-type` to skip auto-detect |
| "Modal" or "container" | Check Modal account: `modal token verify` |
| "push_model" or "upload" | ModelScope upload issue; check MODELSCOPE_TOKEN permissions |

## Edge Cases

- **Repo already exists on destination**: Migration proceeds — files are updated/overwritten.
- **Private source repo**: Works if the source token has read access.
- **Spaces to ModelScope**: Space files are uploaded as a model repo (ModelScope has no Spaces equivalent).
- **Large repos (>10GB)**: May take a while. The Modal function has a 3600s (1 hour) timeout.
- **User provides full URL instead of repo ID**: Extract the `namespace/name` part. Examples:
  - `https://huggingface.co/Linaqruf/model` -> `Linaqruf/model` (platform: hf)
  - `https://modelscope.cn/models/damo/model` -> `damo/model` (platform: ms)

## Scripts Reference

| Script | Purpose | Run As |
|--------|---------|--------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py` | Modal app with migration functions | `modal run ...` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py` | Check all platform tokens | `python ...` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/utils.py` | Shared helpers (imported by other scripts) | N/A (library) |
