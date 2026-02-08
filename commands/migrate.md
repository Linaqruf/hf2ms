---
description: Migrate repos between HuggingFace and ModelScope via Modal
argument-hint: "<source-repo> [--to hf|ms] [--type model|dataset|space] [--dest namespace/name] [--detach]"
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
5. **--detach** flag — run in fire-and-forget mode. Migration continues in Modal's cloud even after the local process exits.

### Examples

| Input | Source | Direction | Type |
|-------|--------|-----------|------|
| `alice/my-model --to ms` | `alice/my-model` | HF→MS | auto-detect |
| `hf:alice/my-model --to ms` | `alice/my-model` | HF→MS | auto-detect |
| `damo/text-to-video --to hf --type model` | `damo/text-to-video` | MS→HF | model |
| `alice/my-dataset --to ms --type dataset` | `alice/my-dataset` | HF→MS | dataset |
| `alice/my-model --to ms --dest OrgName/model-v2` | `alice/my-model` | HF→MS (dest: `OrgName/model-v2`) | auto-detect |
| `alice/my-model --to ms --detach` | `alice/my-model` | HF→MS (detached) | auto-detect |

If the argument is empty or cannot be parsed, ask the user:

```
What repo would you like to migrate? Please provide:
- The repo ID (e.g., username/model-name)
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

**Important:** Note the authenticated usernames from the output (e.g., "Authenticated as: alice"). These will be needed for the destination namespace step.

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

### Step 3: Confirm Destination

Do NOT assume the destination namespace matches the source. If the user did not provide `--dest`, ask where to upload. Use the authenticated username from Step 1 as the suggested default:

```typescript
{
  question: "Where should the repo be uploaded? Your authenticated [Platform] account is [username].",
  header: "Destination",
  options: [
    { label: "[username]/[repo-name]", description: "Upload to your personal account" },
    { label: "Same as source", description: "Use [source-namespace]/[repo-name] (may fail if you don't own this namespace)" },
    { label: "Custom namespace", description: "Specify a different org or account" }
  ]
}
```

If the user picks "Custom namespace", ask for the full `namespace/repo-name`.

Skip this step if the user already provided `--dest` explicitly.

### Step 4: Confirm Migration

Before running, always confirm with the user. Show:

```
Migration Summary:
  Source:      [Platform] / [repo-id] ([type])
  Destination: [Platform] / [dest-repo-id]

Proceed?
```

If the user passed `--detach` in the arguments, skip the run mode question and use detached mode directly. Otherwise, use AskUserQuestion:

```typescript
{
  question: "Ready to start migration?",
  header: "Confirm",
  options: [
    { label: "Yes, migrate", description: "Start the cloud migration via Modal and wait for the result" },
    { label: "Yes, detached", description: "Fire & forget — migration runs in Modal's cloud, free up this session immediately" },
    { label: "Change settings", description: "Modify source, destination, or type" },
    { label: "Cancel", description: "Abort migration" }
  ]
}
```

If the user chooses "Change settings", ask what to change and re-confirm.

Record whether the user chose detached mode — this affects Step 5 (command) and Step 6 (reporting).

### Step 5: Run Migration

Load `.env` and execute the Modal migration command. Always use `::main` entrypoint and set `PYTHONIOENCODING=utf-8` (prevents Unicode errors from Modal CLI on Windows).

**Attached mode** (default):

```bash
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "<source-repo>" --to <hf|ms> --repo-type <type> --dest "<dest-repo>"
```

**Detached mode** (fire & forget) — prepend `--detach` before the script path:

```bash
set -a && source "${CLAUDE_PLUGIN_ROOT}/.env" 2>/dev/null; set +a; PYTHONIOENCODING=utf-8 modal run --detach "${CLAUDE_PLUGIN_ROOT}/scripts/modal_migrate.py::main" --source "<source-repo>" --to <hf|ms> --repo-type <type> --dest "<dest-repo>"
```

Build the command from the parsed arguments:
- Always include `--source` and `--to`
- Always use `::main` entrypoint (not bare `modal_migrate.py`)
- Include `--repo-type` only if the user specified it (otherwise let it auto-detect)
- Always include `--dest` with the confirmed destination from Step 3
- If detached mode: add `--detach` between `modal run` and the script path (it is a `modal run` flag, not a script argument)

### Step 6: Report Result

#### Attached mode

After the command completes:

**On success**: Report the destination URL and file count. Example:
```
Migration complete!
  URL:   https://modelscope.cn/models/username/model-name
  Files: 42
```

**On failure**: Show the error output from Modal and suggest troubleshooting:
- Token issues → re-run validate_tokens.py
- Repo not found → check the repo ID
- Timeout → repo may be too large, suggest `--type` flag if auto-detect timed out
- Modal errors → check Modal account/quota with `modal token verify`

#### Detached mode

After the `modal run --detach` command returns (which happens quickly after the app is launched), report:

```
Migration launched in detached mode (fire & forget).
The migration is running in Modal's cloud and will continue even if this session ends.

Monitor your migration:
  modal app logs hf-ms-migrate      # stream logs in real-time
  modal app list                    # see running/recent apps
  modal app stop hf-ms-migrate     # cancel if needed
  https://modal.com/apps            # web dashboard

No further action needed in this session.
```

Do NOT wait for the migration to complete. Do NOT attempt to poll or check the result. The session is done — the user can check Modal's dashboard or CLI independently.
