---
description: Migrate repos between HuggingFace and ModelScope via Modal
argument-hint: "<source-repo> [--to hf|ms] [--type model|dataset|space] [--dest namespace/name]"
allowed-tools: [Bash, Read, Glob, AskUserQuestion]
---

# /migrate Command

Migrate a HuggingFace or ModelScope repo using Modal as cloud compute. No files touch the local machine.

## Argument Parsing

Parse the user's argument string to extract:

1. **Source repo** — required. Format: `namespace/repo-name` or with platform prefix `hf:namespace/repo` / `ms:namespace/repo`
2. **--to** flag — destination platform: `hf` or `ms`. If source has a platform prefix, infer destination (hf→ms, ms→hf). If neither prefix nor --to, ask.
3. **--type** flag — `model`, `dataset`, or `space`. If omitted, auto-detect.
4. **--dest** flag — custom destination repo ID. Defaults to same as source.

### Examples

| Input | Source | Direction | Type |
|-------|--------|-----------|------|
| `Linaqruf/animagine-xl-3.1 --to ms` | `Linaqruf/animagine-xl-3.1` | HF→MS | auto-detect |
| `hf:Linaqruf/model --to ms` | `Linaqruf/model` | HF→MS | auto-detect |
| `damo/text-to-video --to hf --type model` | `damo/text-to-video` | MS→HF | model |
| `Linaqruf/dataset --to ms --type dataset` | `Linaqruf/dataset` | HF→MS | dataset |
| `Linaqruf/model --to ms --dest MyOrg/model-v2` | `Linaqruf/model` | HF→MS (dest: `MyOrg/model-v2`) | auto-detect |

If the argument is empty or cannot be parsed, ask the user:

```
What repo would you like to migrate? Please provide:
- The repo ID (e.g., Linaqruf/animagine-xl-3.1)
- The direction (to HuggingFace or to ModelScope)
```

## Workflow

Follow these steps in order:

### Step 1: Validate Tokens

Load environment variables from `.env` (if present), then run the token validation script:

```bash
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_tokens.py"
```

If any tokens are missing or invalid, show the user the output and stop. Do NOT proceed without valid tokens.

**Important:** Note the authenticated HuggingFace username from the output (e.g., "Authenticated as: Linaqruf"). When the destination is HuggingFace, use this exact username (case-sensitive) as the default destination namespace — do NOT assume the source repo's namespace matches the HF account.

### Step 2: Determine Direction

If the direction cannot be determined from the arguments (no `--to` flag, no platform prefix), ask:

```typescript
{
  question: "Which direction should the migration go?",
  header: "Direction",
  options: [
    { label: "HuggingFace → ModelScope", description: "Download from HF, upload to ModelScope" },
    { label: "ModelScope → HuggingFace", description: "Download from ModelScope, upload to HF" }
  ]
}
```

### Step 3: Confirm Migration

Before running, always confirm with the user. Show:

```
Migration Summary:
  Source:      [Platform] / [repo-id] ([type])
  Destination: [Platform] / [dest-repo-id]

Proceed?
```

Use AskUserQuestion:

```typescript
{
  question: "Ready to start migration?",
  header: "Confirm",
  options: [
    { label: "Yes, migrate", description: "Start the cloud migration via Modal" },
    { label: "Change settings", description: "Modify source, destination, or type" },
    { label: "Cancel", description: "Abort migration" }
  ]
}
```

If the user chooses "Change settings", ask what to change and re-confirm.

### Step 4: Run Migration

Load `.env` and execute the Modal migration command. Always use `::main` entrypoint and set `PYTHONIOENCODING=utf-8` (prevents Unicode errors from Modal CLI on Windows):

```bash
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "<source-repo>" --to <hf|ms> --repo-type <type> --dest "<dest-repo>"
```

Build the command from the parsed arguments:
- Always include `--source` and `--to`
- Always use `::main` entrypoint (not bare `modal_migrate.py`)
- Include `--repo-type` only if the user specified it (otherwise let it auto-detect)
- Include `--dest` only if different from source

### Step 5: Report Result

After the command completes:

**On success**: Report the destination URL and file count. Example:
```
Migration complete!
  URL:   https://modelscope.cn/models/Linaqruf/animagine-xl-3.1
  Files: 42
```

**On failure**: Show the error output from Modal and suggest troubleshooting:
- Token issues → re-run validate_tokens.py
- Repo not found → check the repo ID
- Timeout → repo may be too large, suggest `--type` flag if auto-detect timed out
- Modal errors → check Modal account/quota with `modal token verify`
