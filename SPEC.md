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
- **Primary**: Plugin author (Linaqruf) — personal workflow tool
- **Secondary**: A friend who may also use it
- **Technical Level**: Developer

### Success Criteria
- [x] Migrate a model repo from HF to ModelScope without any files touching the local machine
- [ ] Migrate in reverse (ModelScope to HF) with the same command
- [x] Support all three HF repo types: models, datasets, spaces
- [x] Complete a typical model migration (~5GB) in under 10 minutes wall-clock time

> **Test result**: hitokomoru-diffusion-v2 — 67 files, 15.6 GB, 7m30s total (1m20s download, 6m10s upload)

---

## Product Requirements

### Core Features (MVP)

#### Feature 1: HuggingFace → ModelScope Migration
**Description**: Download a HuggingFace repo on Modal and upload it to ModelScope.
**User Story**: As a developer, I want to mirror my HF repos to ModelScope so that my models are accessible on both platforms.
**Acceptance Criteria**:
- [ ] Accepts a HuggingFace repo ID (e.g., `Linaqruf/animagine-xl-3.1`)
- [ ] Auto-detects repo type (model/dataset/space) or accepts explicit type
- [ ] Creates the target ModelScope repo if it doesn't exist
- [ ] Transfers all files from source to destination via Modal container
- [ ] Reports progress (downloading... uploading... done)
- [ ] Outputs the destination repo URL on success

#### Feature 2: ModelScope → HuggingFace Migration
**Description**: Download a ModelScope repo on Modal and upload it to HuggingFace.
**User Story**: As a developer, I want to pull repos from ModelScope to HuggingFace so I can consolidate or share on either platform.
**Acceptance Criteria**:
- [ ] Accepts a ModelScope model/dataset ID
- [ ] Creates the target HuggingFace repo if it doesn't exist
- [ ] Transfers all files via Modal container
- [ ] Reports progress and outputs destination URL

#### Feature 3: Credential Validation
**Description**: Verify all three platform tokens before starting migration.
**User Story**: As a developer, I want early feedback if my tokens are invalid so I don't waste time on a migration that will fail halfway.
**Acceptance Criteria**:
- [ ] Checks `HF_TOKEN`, `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`, `MODELSCOPE_TOKEN` from environment
- [ ] Reports which tokens are missing or invalid before starting any transfer
- [ ] Provides clear instructions for obtaining each token

#### Feature 4: Claude Code Skill Integration
**Description**: A skill that triggers when the user asks to migrate repos, guiding the workflow conversationally.
**User Story**: As a developer, I want to say "migrate this model to ModelScope" in Claude Code and have it just work.
**Acceptance Criteria**:
- [ ] Skill triggers on natural language like "migrate", "transfer", "push to ModelScope/HuggingFace"
- [ ] Slash command `/migrate` available as explicit entry point
- [ ] Skill asks for source repo if not provided
- [ ] Skill asks for direction if ambiguous
- [ ] Skill runs the Modal script and reports results

### Future Scope (Post-MVP)
1. Batch migration (migrate all repos from an org/user)
2. Model format conversion during migration (e.g., safetensors to GGUF)
3. Selective file migration (filter by pattern, e.g., only `.safetensors`)
4. Persistent Modal Volume for caching frequently transferred repos
5. Bidirectional sync (keep repos in sync automatically)
6. Dry-run mode (show what would be transferred without doing it)

### Out of Scope
- Model format conversion or quantization
- Batch/bulk migration of entire organizations
- Automated scheduling or cron-based sync
- Web UI or dashboard
- HuggingFace Spaces deployment (only file transfer)

### User Flows

#### Primary Flow: Migrate a Repo

```
User: "migrate Linaqruf/animagine-xl-3.1 to ModelScope"
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
Confirm with user: "Migrate model Linaqruf/animagine-xl-3.1 → ModelScope as Linaqruf/animagine-xl-3.1?"
  │
  ▼
Run Modal function:
  ├── Create target repo on ModelScope (if needed)
  ├── snapshot_download from HuggingFace
  ├── Upload folder to ModelScope
  └── Return result
  │
  ▼
Report: "Done! https://modelscope.ai/models/Linaqruf/animagine-xl-3.1"
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
3. Validate all required tokens exist and are valid (API call to each platform's `/whoami` or equivalent)
4. Determine destination repo ID (default: same name under user's namespace)
5. Create destination repo if it doesn't exist (`create_repo` with `exist_ok=True`)
6. Invoke Modal function with: source_id, dest_id, direction, repo_type, tokens
7. Modal function: `snapshot_download` from source → `/tmp/repo` → `upload_folder` to destination
8. Return destination URL

**Edge cases**:
- Source repo doesn't exist → fail with "Repo not found" before invoking Modal
- Destination repo already exists → proceed (upload will update files), warn user
- Large repo (>50GB) → warn user about potential timeout, suggest `allow_patterns` filter in future
- Private source repo → works if token has read access
- Rate limit hit → Modal function retries with backoff (3 attempts max)

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
│       └── SKILL.md            # Migration skill (triggers on natural language)
│
├── scripts/
│   ├── modal_migrate.py        # Modal app: migration functions
│   ├── validate_tokens.py      # Token validation utility
│   └── utils.py                # Shared helpers (repo ID parsing, etc.)
│
└── README.md                   # Setup instructions (optional, for friend)
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
modal run scripts/modal_migrate.py --source "Linaqruf/animagine-xl-3.1" --to ms

# Explicit type, custom destination name
modal run scripts/modal_migrate.py --source "Linaqruf/model" --to ms --repo-type model --dest "Linaqruf/model-v2"

# ModelScope → HuggingFace
modal run scripts/modal_migrate.py --source "damo/text-to-video" --to hf

# Platform prefix instead of --to flag
modal run scripts/modal_migrate.py --source "hf:Linaqruf/model" --to ms
```

### Remote Functions

Four Modal functions run in the cloud container:
- `hello_world` — smoke test (60s timeout)
- `detect_repo_type` — auto-detect model/dataset/space via API (120s timeout)
- `migrate_hf_to_ms` — HF→MS transfer (3600s timeout, uses `create_model`/`create_dataset` + `upload_folder`)
- `migrate_ms_to_hf` — MS→HF transfer (3600s timeout, uses `create_repo` + `upload_folder`)

**Important**: Utils imports (`from utils import ...`) must be lazy (inside `main()`) because Modal only auto-mounts the entrypoint file. The remote functions don't use utils.

### Plugin Manifest (.claude-plugin/plugin.json)

```json
{
  "name": "hf-modal-modelscope",
  "version": "1.0.0",
  "description": "Migrate repos between HuggingFace and ModelScope via Modal — no local downloads.",
  "license": "MIT",
  "author": { "name": "Linaqruf" },
  "keywords": ["huggingface", "modelscope", "modal", "migration"]
}
```

### Skill Trigger Patterns

The skill should trigger on:
- "migrate [repo] to ModelScope/HuggingFace"
- "transfer [repo] to ModelScope/HuggingFace"
- "push [repo] to ModelScope/HuggingFace"
- "copy [repo] from HuggingFace to ModelScope"
- "mirror [repo]"

### Slash Command: `/migrate`

```
/migrate <source-repo> [--to hf|ms] [--type model|dataset|space]
```

Examples:
- `/migrate Linaqruf/animagine-xl-3.1 --to ms`
- `/migrate damo/text-to-video --to hf --type model`
- `/migrate Linaqruf/my-dataset --to ms --type dataset`

---

## Error Handling Strategy

| Error | Detection | Recovery |
|-------|-----------|----------|
| Missing token | Check env vars before Modal invocation | Print which token is missing + link to obtain it |
| Invalid token | API whoami call returns 401 | Print "Token invalid" + re-check instructions |
| Source repo not found | HF/MS API returns 404 | Print "Repo not found" + suggest checking the ID |
| Destination repo creation fails | API error on create_repo | Print error + suggest checking namespace/permissions |
| Download timeout | Modal function times out (>3600s) | Print "Repo too large for single transfer" + suggest filtering |
| Upload failure mid-transfer | API error during upload | Print error + note partial upload may exist on destination |
| Modal cold start fails | Modal container build fails | Print error + suggest checking Modal account/quota |
| Network error | Connection timeout/reset | Retry up to 3 times with exponential backoff |

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
- [ ] Test with a dataset repo

### Phase 3: Plugin Integration
**Depends on**: Phase 2 (migration functions must work)
- [x] Write `/migrate` slash command (`commands/migrate.md`) — full argument parsing, 5-step workflow
- [x] Write migration skill with trigger patterns (`skills/migrate/SKILL.md`) — conversational extraction, confirmation flow
- [x] Wire skill to invoke `modal run scripts/modal_migrate.py` via Bash
- [x] Handle argument parsing (source repo, direction, type, custom dest, URL extraction)
- [x] Add user confirmation step before migration starts (AskUserQuestion with confirm/change/cancel)

### Phase 4: Polish & Testing
**Depends on**: Phase 3 (plugin must be functional end-to-end)
- [x] Add progress reporting (file count, size, download/upload timing, total duration)
- [x] Add error handling (try/except with contextual troubleshooting suggestions)
- [x] Test with larger repos (15.6 GB model — hitokomoru-diffusion-v2)
- [ ] Test all repo types (model done, dataset and space pending)
- [ ] Test both directions (HF→MS done, MS→HF pending)
- [ ] Test error cases (bad token, missing repo, network failure)
- [x] Write README with setup instructions

---

## Open Questions

| # | Question | Options | Impact | Status |
|---|----------|---------|--------|--------|
| 1 | ModelScope SDK version — older `modelscope` vs newer `modelhub` API? | Use `modelscope.hub.api.HubApi` — `create_model()` + `upload_folder()` (HTTP-based, no git). `push_model()` was deprecated and required git. | Affects upload implementation in Modal function | Resolved |
| 2 | ModelScope repo naming — does namespace differ from HF? | A) Map HF username → MS username directly, B) Ask user for MS namespace | Affects auto-naming of destination repos | Open |
| 3 | Space migration — ModelScope doesn't have "Spaces" equivalent | A) Skip space type for MS direction, B) Upload space files as a model repo | Affects feature completeness | Open |
| 4 | Large file handling — what if a repo has files >50GB? | A) Let it fail with timeout, B) Implement chunked/resumable upload | Affects reliability for large models | Open |
| 5 | Modal timeout — 3600s enough for large repos? | A) Use 3600s default, B) Make configurable | Affects large model transfers | Resolved — 15.6 GB completed in 7m30s, well within 3600s |

---

## References

### External Documentation
- [Modal Docs — Functions & Images](https://modal.com/docs/guide)
- [huggingface_hub — Download & Upload](https://huggingface.co/docs/huggingface_hub/guides/download)
- [ModelScope Hub API](https://modelscope.ai/docs)
- [Claude Code Plugin Structure](https://docs.anthropic.com/en/docs/claude-code/plugins)

---

*Generated with project-spec plugin for Claude Code*
