# HF2MS — HuggingFace to ModelScope Migration Plugin

> Claude Code plugin that migrates HuggingFace repos to ModelScope (and vice versa) using Modal as cloud compute — no local downloads required.
>
> GitHub: https://github.com/Linaqruf/hf2ms

## Overview

### Problem Statement
Migrating ML models, datasets, and spaces between HuggingFace and ModelScope requires downloading large files locally or maintaining a dedicated server (e.g., RunPod). This wastes bandwidth, disk space, and time.

### Solution
A Claude Code plugin that orchestrates cloud-to-cloud migration using Modal as an ephemeral compute bridge. The user says "migrate this repo" and the plugin handles everything — downloading from the source platform and uploading to the destination platform entirely in the cloud.

### Target Users
- **Primary**: ML developers who maintain repos on both HuggingFace and ModelScope
- **Secondary**: Teams needing to mirror model/dataset repos across platforms
- **Technical Level**: Developer (CLI-comfortable, has platform tokens)

### Success Criteria
- [x] Migrate a model repo from HF to ModelScope without any files touching the local machine
- [x] Migrate in reverse (ModelScope to HF) with the same command — tested: furina-xl-lora 163 MB, 18.2s
- [x] Support all three HF repo types: models, datasets, spaces
- [x] Complete a typical model migration (~5GB) in under 10 minutes wall-clock time
- [x] Batch migrate multiple repos in parallel (17 models + 3 datasets = 20 repos migrated)

> **Test results**:
> - Single model HF→MS: hitokomoru-diffusion-v2 — 67 files, 15.6 GB, 7m30s
> - Single model MS→HF: furina-xl-lora — 163 MB, 18.2s (detached)
> - Single model HF→MS detached: furina-xl-lora — 163 MB, 9.2s
> - Single dataset: proseka-card-list — 7 files, 2.2 GB, 14m11s
> - Batch models: 17 models, ~189 GB, 43m44s (parallel containers)
> - Batch datasets: pixiv-niji-journey — 16 files, 58.5 GB, 19m48s; bandori-card-dataset — 3 files, 2.3 GB (migrated separately)
> - Space migration: skipped to MS with warning (ModelScope Studios are web/git only)
> - Error cases: nonexistent repo gives clean error; all token validations pass

---

## Product Requirements

### Core Features (MVP)

#### Feature 1: HuggingFace → ModelScope Migration
**Description**: Download a HuggingFace repo on Modal and upload it to ModelScope.
**User Story**: As a developer, I want to mirror my HF repos to ModelScope so that my models are accessible on both platforms.
**Acceptance Criteria**:
- [x] Accepts a HuggingFace repo ID (e.g., `username/my-model`)
- [x] Auto-detects repo type (model/dataset/space) or accepts explicit type
- [x] Creates the target ModelScope repo if it doesn't exist
- [x] Transfers all files from source to destination via Modal container
- [x] Reports progress (downloading... uploading... done)
- [x] Outputs the destination repo URL on success

#### Feature 2: ModelScope → HuggingFace Migration
**Description**: Download a ModelScope repo on Modal and upload it to HuggingFace.
**User Story**: As a developer, I want to pull repos from ModelScope to HuggingFace so I can consolidate or share on either platform.
**Acceptance Criteria**:
- [x] Accepts a ModelScope model/dataset ID
- [x] Creates the target HuggingFace repo if it doesn't exist
- [x] Transfers all files via Modal container
- [x] Reports progress and outputs destination URL
- [x] End-to-end tested (furina-xl-lora MS→HF, 163 MB, 18.2s detached)

#### Feature 3: Credential Validation
**Description**: Verify all three platform tokens before starting migration.
**User Story**: As a developer, I want early feedback if my tokens are invalid so I don't waste time on a migration that will fail halfway.
**Acceptance Criteria**:
- [x] Checks `HF_TOKEN`, `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`, `MODELSCOPE_TOKEN` from environment
- [x] Reports which tokens are missing or invalid before starting any transfer
- [x] Provides clear instructions for obtaining each token

#### Feature 4: Claude Code Skill Integration
**Description**: A skill that triggers when the user asks to migrate repos, guiding the workflow conversationally.
**User Story**: As a developer, I want to say "migrate this model to ModelScope" in Claude Code and have it just work.
**Acceptance Criteria**:
- [x] Skill triggers on natural language like "migrate", "transfer", "push to ModelScope/HuggingFace", "batch migrate"
- [x] Slash command `/migrate` available as explicit entry point
- [x] Skill asks for source repo if not provided
- [x] Skill asks for direction if ambiguous
- [x] Skill asks for destination namespace (never assumes)
- [x] Skill runs the Modal script and reports results

#### Feature 5: Batch Migration
**Description**: Migrate multiple repos in parallel using Modal's `starmap()`.
**User Story**: As a developer, I want to migrate all my repos at once instead of one by one.
**Acceptance Criteria**:
- [x] Accepts comma-separated repo IDs
- [x] Each repo runs in its own parallel container
- [x] Pre-checks destination existence, skips repos that already exist
- [x] Reports per-repo status and overall summary with success/fail/skipped counts

#### Feature 6: Destination Existence Check
**Description**: Check if destination repo already exists before migrating.
**User Story**: As a developer, I don't want to waste time re-migrating repos that are already on the destination.
**Acceptance Criteria**:
- [x] Single mode: warns if destination exists, proceeds with overwrite
- [x] Batch mode: skips existing repos automatically
- [x] Checks run in parallel for batch operations

#### Feature 7: Detached Migration Mode (`--detach`)
**Description**: Run migrations in fire-and-forget mode using Modal's `--detach` flag. The migration continues in Modal's cloud even after the local process exits.
**User Story**: As a developer, I want to launch a migration and move on to other work without keeping my terminal open or Claude session active, saving tokens and time.
**Acceptance Criteria**:
- [x] User can choose between attached (wait for result) and detached (fire & forget) mode
- [x] Detached mode adds `--detach` flag to `modal run` command
- [x] After launching detached, Claude prints the app name and monitoring commands, then finishes
- [x] `/migrate` command confirmation step offers detached option
- [x] Batch migrations support detached mode
- [x] Documentation includes monitoring commands (`modal app logs`, `modal app list`, `modal app stop`)

### Future Scope (Post-MVP)
1. ~~Batch migration~~ — **Done.** `batch` entrypoint with `starmap()` for parallel containers. Tested: 20 repos, ~252 GB.
2. ~~Destination existence check~~ — **Done.** Single mode warns, batch mode auto-skips existing repos.
3. Programmatic spawn/poll pattern — Use `.spawn()` + `FunctionCall.from_id()` for async status checks within Claude (requires `modal deploy`)
4. Model format conversion during migration (e.g., safetensors to GGUF)
5. Selective file migration (`--allow-patterns` / `--ignore-patterns` flags)
6. Persistent Modal Volume for caching frequently transferred repos
7. Bidirectional sync (keep repos in sync automatically)
8. Dry-run mode (show what would be transferred without doing it)
9. `--force` flag to overwrite existing destination repos in batch mode

### Out of Scope
- Model format conversion or quantization
- Automated scheduling or cron-based sync
- Web UI or dashboard
- HuggingFace Spaces deployment (only file transfer)

### User Flows

#### Primary Flow: Migrate a Repo

```
User: "migrate username/my-model to ModelScope"
  │
  ▼
Skill activates → parses source repo + direction
  │
  ▼
Validate credentials (HF_TOKEN, MODAL, MODELSCOPE_TOKEN)
  │  ✗ → Report missing tokens with setup instructions
  ▼
Detect repo type (model/dataset/space)
  │
  ▼
Confirm destination: "Where should the repo be uploaded?"
  │  → User's account, same as source, or custom namespace
  ▼
Confirm with user: "Migrate model username/my-model → ModelScope as username/my-model?"
  │
  ▼
Choose run mode: attached (wait) or detached (fire & forget)
  │
  ├── Attached: Run Modal function, wait for result
  │     ├── Create target repo on ModelScope (if needed)
  │     ├── snapshot_download from HuggingFace
  │     ├── Upload folder to ModelScope
  │     └── Report: "Done! https://modelscope.ai/models/username/my-model"
  │
  └── Detached: Run with `modal run --detach`
        ├── Migration launched in background
        ├── Print: "Check logs: modal app logs hf-ms-migrate"
        └── Claude session ends — user checks Modal dashboard/CLI later
```

#### Reverse Flow: ModelScope → HuggingFace

```
User: "migrate modelscope:damo/some-model to HuggingFace"
  │
  ▼
Same flow, reversed source/destination SDKs
```

---

## Technical Architecture

### Tech Stack

| Layer | Technology | Rationale | Alternatives Considered |
|-------|------------|-----------|------------------------|
| Plugin Format | Claude Code Plugin | Native integration, skill-based triggers, conversational UX | Standalone CLI (less integrated), MCP server (overkill for this) |
| Cloud Compute | Modal | Serverless, ephemeral containers, Python-native, no infra management | RunPod (requires persistent server), AWS Lambda (size limits), Google Cloud Run (more setup) |
| HF SDK | `huggingface_hub` | Official Python client, `snapshot_download` + `upload_folder` | Raw API calls (more work), `git clone` (slower, needs git-lfs) |
| MS SDK | `modelscope` | Official Python client, Hub API for create/upload | ModelScope Git (requires git), raw REST API (undocumented) |
| Script Language | Python | Required by Modal SDK, HF Hub SDK, and ModelScope SDK | N/A — all three platforms are Python-first |

### Architecture Diagram

```
┌──────────────┐        ┌─────────────────────────────┐        ┌──────────────┐
│  HuggingFace │◀──────▶│      Modal Container        │◀──────▶│  ModelScope  │
│     Hub      │  API   │                             │  API   │     Hub      │
│              │        │  huggingface_hub.snapshot_   │        │              │
│  - Models    │        │    download()                │        │  - Models    │
│  - Datasets  │        │  modelscope.hub.HubApi.     │        │  - Datasets  │
│  - Spaces    │        │    upload_folder()            │        │              │
└──────────────┘        └─────────────────────────────┘        └──────────────┘
                                     ▲
                                     │ `modal run`
                                     │
                        ┌────────────────────────┐
                        │   Local Machine        │
                        │                        │
                        │   Claude Code Plugin   │
                        │   ├── /migrate command  │
                        │   ├── migrate skill     │
                        │   └── modal_migrate.py  │
                        └────────────────────────┘
```

### Migration State Diagram

```
┌───────────┐
│  pending   │ ← User confirmed migration
└─────┬─────┘
      │ validate credentials
      ▼
┌───────────┐
│ validating │
└─────┬─────┘
      │ credentials OK
      ▼
┌───────────┐
│downloading │ ← snapshot_download on Modal
└─────┬─────┘
      │ download complete
      ▼
┌───────────┐
│ uploading  │ ← upload_folder / push to destination
└─────┬─────┘
      │ upload complete
      ▼
┌───────────┐
│ completed  │ → Report destination URL
└───────────┘

  Any state → [failed] → Report error + cleanup
```

---

## Algorithm: Migration Orchestration

**Input**: Source repo ID, direction (hf→ms or ms→hf), repo type (auto-detect or explicit)
**Output**: Destination repo URL or error message

**Steps**:
1. Parse source repo ID and direction from user input
2. If repo type not specified, detect via HF API (`model_info` / `dataset_info`) or ModelScope API
3. Check that all required token environment variables (`HF_TOKEN`, `MODELSCOPE_TOKEN`) are set and non-empty
4. Confirm destination repo ID with user (suggest authenticated username as default, never assume)
5. Create destination repo if it doesn't exist (HF: `create_repo` with `exist_ok=True`; MS: `repo_exists()` then `create_model()`/`create_dataset()`)
6. Invoke Modal function with: source_id, dest_id, direction, repo_type, tokens
7. Modal function: `snapshot_download` from source → temp directory → `upload_folder` to destination
8. Return destination URL

**Edge cases**:
- Source repo doesn't exist → if auto-detecting type, `detect_repo_type` raises error on Modal container; if type is explicit, fails during `snapshot_download`
- Destination repo already exists → single mode warns and proceeds (updates files); batch mode skips existing repos
- Large repo (>50GB) → warn user about potential timeout, suggest `allow_patterns` filter in future
- Private source repo → works if token has read access
- Rate limit hit → migration fails with error message; no automatic retries

---

## Environment Variables

| Variable | Description | Required | How to Obtain |
|----------|-------------|----------|---------------|
| `HF_TOKEN` | HuggingFace access token (read + write) | Yes | https://huggingface.co/settings/tokens |
| `MODAL_TOKEN_ID` | Modal token ID | Yes | `modal token new` or https://modal.com/settings |
| `MODAL_TOKEN_SECRET` | Modal token secret | Yes | Same as above |
| `MODELSCOPE_TOKEN` | ModelScope API token | Yes | https://modelscope.ai/my/myaccesstoken |
| `MODELSCOPE_DOMAIN` | ModelScope domain (bare, no protocol) | No | Default: `modelscope.cn`. Set to `modelscope.ai` for international site |

---

## File Structure

```
hf2ms/
├── .claude-plugin/
│   └── plugin.json             # Claude Code plugin manifest
├── SPEC.md                     # This specification
├── CLAUDE.md                   # Agent pointer file
├── .env.example                # Environment variable template
│
├── commands/
│   └── migrate.md              # /migrate slash command
│
├── skills/
│   └── migrate/
│       ├── SKILL.md            # Migration skill (triggers on natural language)
│       └── references/
│           └── hub-api-reference.md  # HuggingFace & ModelScope SDK reference
│
├── scripts/
│   ├── modal_migrate.py        # Modal app: migration functions
│   ├── validate_tokens.py      # Token validation utility
│   └── utils.py                # Shared helpers (repo ID parsing, etc.)
│
└── README.md                   # User-facing documentation (setup, usage, troubleshooting)
```

---

## Key Implementation Details

### Modal Container Image

Minimal Python image with only the required SDKs:

```python
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "huggingface_hub",
        "modelscope",
    )
)
```

No torch, no transformers — just the hub clients. This keeps cold start fast (~10-15s).

### Local Entrypoint (CLI Orchestration)

The `@app.local_entrypoint()` runs on your machine, reads env tokens, and calls remote functions:

```bash
# Auto-detect repo type, migrate to ModelScope
modal run scripts/modal_migrate.py::main --source "username/my-model" --to ms

# Explicit type, custom destination name
modal run scripts/modal_migrate.py::main --source "username/my-model" --to ms --repo-type model --dest "OrgName/model-v2"

# ModelScope → HuggingFace
modal run scripts/modal_migrate.py::main --source "damo/text-to-video" --to hf

# Platform prefix instead of --to flag
modal run scripts/modal_migrate.py::main --source "hf:username/my-model" --to ms

# Fire & forget — migration continues after terminal disconnects
modal run --detach scripts/modal_migrate.py::main --source "username/my-model" --to ms

# Check on a detached migration
modal app logs hf-ms-migrate    # stream logs
modal app list                  # see running/recent apps
modal app stop hf-ms-migrate    # cancel a running migration
```

**Note**: `--detach` is a `modal run` flag (before the script path), not a script argument. The local entrypoint still runs to parse tokens and set up the migration — but with `--detach`, after the remote functions are invoked, the local process exits while the cloud containers continue running independently.

### Remote Functions

Five Modal functions run in the cloud container:
- `hello_world` — smoke test (60s timeout)
- `check_repo_exists` — check if a repo exists on HF or MS (120s timeout); catches only `RepositoryNotFoundError` for HF, lets other errors propagate
- `detect_repo_type` — auto-detect model/dataset/space via API (120s timeout); HF: catches only 404s, surfaces auth/network errors; MS: checks model then dataset, surfaces non-404 errors
- `migrate_hf_to_ms` — HF→MS transfer (3600s timeout, uses `create_model`/`create_dataset` + `upload_folder`); includes full traceback on error
- `migrate_ms_to_hf` — MS→HF transfer (3600s timeout, uses `create_repo` + `upload_folder`); passes `repo_type` to MS download

**Important**: Utils imports (`from utils import ...`) must be lazy (inside `main()`) because Modal only auto-mounts the entrypoint file. The remote functions don't use utils.

### Batch Entrypoint

The `batch` local entrypoint accepts comma-separated repo IDs and fans them out to parallel containers using Modal's `starmap()`:

```bash
modal run scripts/modal_migrate.py::batch \
  --source "user/repo1,user/repo2,user/repo3" \
  --to ms --repo-type model
```

- Each repo gets its own container (download + upload happen independently)
- Pre-checks destination repos in parallel; skips any that already exist
- Results stream back as each container completes
- Summary printed at the end with success/fail/skipped counts
- Tested: 17 models (~189 GB) in 43m44s; 3 datasets (~63 GB) including 58.5 GB pixiv-niji-journey in 19m48s

### Plugin Manifest (.claude-plugin/plugin.json)

```json
{
  "name": "hf-modal-modelscope",
  "version": "1.0.0",
  "description": "Migrate repos between HuggingFace and ModelScope via Modal — no local downloads. Supports models, datasets, and spaces with cloud-to-cloud transfer.",
  "license": "MIT",
  "author": { "name": "Linaqruf", "url": "https://github.com/Linaqruf" },
  "repository": "https://github.com/Linaqruf/hf2ms",
  "homepage": "https://github.com/Linaqruf/hf2ms",
  "keywords": ["huggingface", "modelscope", "modal", "migration", "model-transfer", "cloud-compute", "ml-ops"]
}
```

### Skill Trigger Patterns

The skill should trigger on:
- "migrate [repo] to ModelScope/HuggingFace"
- "transfer [repo] to ModelScope/HuggingFace"
- "push [repo] to ModelScope/HuggingFace"
- "copy [repo] from HuggingFace to ModelScope"
- "mirror [repo]"
- "batch migrate", "migrate multiple repos", "bulk transfer"
- "download from ModelScope", "upload to ModelScope"
- "sync repo between HuggingFace and ModelScope"

### Slash Command: `/migrate`

```
/migrate <source-repo> [--to hf|ms] [--type model|dataset|space] [--dest namespace/name] [--detach]
```

Examples:
- `/migrate username/my-model --to ms`
- `/migrate damo/text-to-video --to hf --type model`
- `/migrate username/my-dataset --to ms --type dataset`
- `/migrate username/my-model --to ms --detach` (fire & forget)

---

## Error Handling Strategy

| Error | Detection | Recovery |
|-------|-----------|----------|
| Missing token | Check env vars before Modal invocation | Print which token is missing + link to obtain it |
| Invalid token | API whoami call returns 401 | Print validation failure with exception type + re-check instructions |
| Source repo not found | HF/MS API returns 404 | Print "Repo not found" + suggest checking the ID |
| Auth/network error in detect | Non-404 exception from API | Surface via `RuntimeError` with original error (not masked as "not found") |
| Destination repo creation fails | API error on create_repo | Print error + suggest checking namespace/permissions |
| Download timeout | Modal function times out (>3600s) | Print "Repo too large for single transfer" + suggest filtering |
| Upload failure mid-transfer | API error during upload | Print error + remote traceback displayed to user |
| Modal cold start fails | Modal container build fails | Print error + suggest checking Modal account/quota |
| Network error | Connection timeout/reset | Migration fails with error + full traceback; no automatic retries |
| Batch auth failure | Pre-check starmap fails with auth error | Abort entire batch (don't proceed blindly) |
| Batch infra failure | Starmap throws mid-execution | Report completed count + list repos with unknown status |
| Detached run — result unknown | `--detach` mode, no local output after launch | Print `modal app logs hf-ms-migrate` command for user to check |

---

## Development Phases

### Phase 1: Foundation
**Depends on**: Nothing
- [x] Initialize plugin structure (`.claude-plugin/plugin.json`, directory layout)
- [x] Create `.env.example` with all required tokens
- [x] Write token validation script (`scripts/validate_tokens.py`)
- [x] Write Modal app skeleton with container image definition (`scripts/modal_migrate.py`)
- [x] Test Modal function deploys and runs (hello world)

### Phase 2: Core Migration
**Depends on**: Phase 1 (Modal app must deploy)
- [x] Implement HF → ModelScope migration function on Modal
- [x] Implement ModelScope → HF migration function on Modal
- [x] Add repo type auto-detection (`detect_repo_type` remote function)
- [x] Add destination repo auto-creation (create_model/create_dataset on MS, create_repo exist_ok on HF)
- [x] Add `@app.local_entrypoint()` for CLI orchestration (reads env tokens, parses args, calls remote)
- [x] Test with a model repo (hitokomoru-diffusion-v2: 67 files, 15.6 GB, 7m30s)
- [x] Test with a dataset repo (proseka-card-list: 7 files, 2.2 GB, 14m11s)
- [x] Batch migration — models (17 repos, ~189 GB, 43m44s with parallel containers)
- [x] Batch migration — datasets (pixiv-niji-journey: 16 files, 58.5 GB, 19m48s; bandori-card-dataset: migrated separately)

### Phase 3: Plugin Integration
**Depends on**: Phase 2 (migration functions must work)
- [x] Write `/migrate` slash command (`commands/migrate.md`) — full argument parsing, 6-step workflow (validate, direction, destination, confirm, run, report)
- [x] Write migration skill with trigger patterns (`skills/migrate/SKILL.md`) — conversational extraction, confirmation flow
- [x] Wire skill to invoke `modal run scripts/modal_migrate.py::main` via Bash
- [x] Handle argument parsing (source repo, direction, type, custom dest, URL extraction)
- [x] Add destination namespace confirmation (never assume — ask user, suggest authenticated username)
- [x] Add user confirmation step before migration starts (AskUserQuestion with confirm/change/cancel)

### Phase 4: Polish & Testing
**Depends on**: Phase 3 (plugin must be functional end-to-end)
- [x] Add progress reporting (file count, size, download/upload timing, total duration)
- [x] Add error handling (try/except with contextual troubleshooting suggestions)
- [x] Test with larger repos (15.6 GB model — hitokomoru-diffusion-v2)
- [x] Test batch migration — models (17 repos, ~189 GB, 43m44s)
- [x] Test batch migration — datasets (3 repos, ~63 GB)
- [x] Test all repo types (model done, dataset done, space — skipped to MS with warning; ModelScope Studios are web/git only)
- [x] Test both directions (HF→MS done, MS→HF done — furina-xl-lora 163 MB, 18.2s)
- [x] Test error cases (nonexistent repo: clean error "Repo not found on HuggingFace as model, dataset, or space")
- [x] Write README with setup instructions

### Phase 5: Detached Migration Mode
**Depends on**: Phase 3 (slash command and skill must exist)
- [x] Update `/migrate` command confirmation step with detached option
- [x] Update `/migrate` command Step 5 to prepend `--detach` flag when chosen
- [x] Update `/migrate` command Step 6 with detached-mode reporting (app name + monitoring commands)
- [x] Update migration skill (`SKILL.md`) to mention detached mode
- [x] Update `CLAUDE.md` with `--detach` usage
- [x] Update `README.md` with detached mode documentation
- [x] Test single migration with `--detach` — HF→MS furina-xl-lora 163 MB, 9.2s detached
- [x] Test batch migration with `--detach` — correctly detected & skipped 2 existing repos

---

## Open Questions

| # | Question | Options | Impact | Status |
|---|----------|---------|--------|--------|
| 1 | ModelScope SDK version — older `modelscope` vs newer `modelhub` API? | Use `modelscope.hub.api.HubApi` — `create_model()` + `upload_folder()` (HTTP-based, no git). `push_model()` was deprecated and required git. | Affects upload implementation in Modal function | Resolved |
| 2 | ModelScope repo naming — does namespace differ from HF? | A) Map HF username → MS username directly, B) Ask user for MS namespace | Affects auto-naming of destination repos | Resolved — same name works fine, `--dest` flag available for custom mapping |
| 3 | Space migration — ModelScope doesn't have "Spaces" equivalent | A) Skip space type for MS direction, B) Upload space files as a model repo | Affects feature completeness | Resolved — spaces to MS are skipped with a warning. ModelScope Studios are web/git only (SDK has `# TODO: support studio`). Users can force with `--repo-type model`. |
| 4 | Large file handling — what if a repo has files >50GB? | A) Let it fail with timeout, B) Implement chunked/resumable upload | Affects reliability for large models | Resolved — 58.5 GB (pixiv-niji-journey) completed in 19m48s with no issues |
| 5 | Modal timeout — 3600s enough for large repos? | A) Use 3600s default, B) Make configurable | Affects large model transfers | Resolved — 58.5 GB in 19m48s, well within 3600s |

---

## References

### External Documentation
- [Modal Docs — Functions & Images](https://modal.com/docs/guide)
- [huggingface_hub — Download & Upload](https://huggingface.co/docs/huggingface_hub/guides/download)
- [ModelScope Hub API](https://modelscope.ai/docs)
- [Claude Code Plugin Structure](https://docs.anthropic.com/en/docs/claude-code/plugins)

---

## Project Status

**All phases complete.** All features implemented, tested, and documented. All open questions resolved. All acceptance criteria met.

- PR #1: Fix error handling, input validation, doc accuracy from codebase review
- PR #2: Add detached mode, README license sanitizer, space rejection, batch space guard, all tests pass

---

*Generated with project-spec plugin for Claude Code*
