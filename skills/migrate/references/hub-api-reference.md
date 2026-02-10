# Hub API Reference — HuggingFace & ModelScope

Inline reference for Python SDK methods — no external documentation lookups needed.

---

## HuggingFace Hub (`huggingface_hub`)

```python
from huggingface_hub import HfApi, snapshot_download, hf_hub_download
api = HfApi(token="hf_xxx")
```

### Authentication

```python
api.whoami(token=None) -> dict
# Returns {"name": "username", "orgs": [...], ...}
```

### List Repos

```python
# Models — returns Iterable[ModelInfo]
api.list_models(author="username", search="xl", pipeline_tag="text-to-image",
                sort="last_modified", direction=-1, limit=50, full=True)
# ModelInfo attrs: .id, .private, .downloads, .likes, .tags, .pipeline_tag, .last_modified

# Datasets — returns Iterable[DatasetInfo]
api.list_datasets(author="username", search="pixiv", sort="last_modified",
                  limit=50, tags=["language:en"])
# DatasetInfo attrs: .id, .private, .downloads, .likes, .tags, .last_modified

# Spaces — returns Iterable[SpaceInfo]
api.list_spaces(author="username", search="animagine", sort="last_modified", limit=50)
# SpaceInfo attrs: .id, .private, .likes, .sdk, .last_modified
```

### Repo Info

```python
# Single repo info (raises 404 if not found)
api.model_info(repo_id, revision=None, files_metadata=False) -> ModelInfo
api.dataset_info(repo_id, revision=None, files_metadata=False) -> DatasetInfo
api.space_info(repo_id, revision=None, files_metadata=False) -> SpaceInfo

# Generic (auto-detects type based on repo_type param)
api.repo_info(repo_id, repo_type="model") -> ModelInfo | DatasetInfo | SpaceInfo

# Check existence (no exception)
api.repo_exists(repo_id, repo_type=None) -> bool
api.file_exists(repo_id, filename, repo_type=None, revision=None) -> bool
```

### Create / Delete Repos

```python
# Create (returns RepoUrl)
api.create_repo(repo_id, private=False, repo_type="model", exist_ok=True)
# repo_type: "model", "dataset", "space"
# For spaces: space_sdk="gradio"|"streamlit"|"docker"|"static"

# Delete (irreversible!)
api.delete_repo(repo_id, repo_type=None, missing_ok=False)

# Move/rename
api.move_repo(from_id="old/name", to_id="new/name", repo_type=None)
```

### Download

```python
# Download entire repo → returns local dir path
snapshot_download(
    repo_id="username/my-model",
    repo_type="model",           # "model", "dataset", "space"
    revision=None,               # branch/tag/commit
    local_dir="/path/to/save",   # explicit output dir
    cache_dir=None,              # use HF cache
    allow_patterns=["*.safetensors"],  # only these files
    ignore_patterns=["*.bin"],         # skip these files
    max_workers=8,
    token=None,
)

# Download single file → returns local file path
hf_hub_download(
    repo_id="username/my-model",
    filename="model.safetensors",
    subfolder=None,
    repo_type="model",
    revision=None,
    local_dir=None,
    cache_dir=None,
    token=None,
)
```

### Upload

```python
# Upload entire folder
api.upload_folder(
    repo_id="username/my-model",
    folder_path="/local/path",
    path_in_repo="",             # subfolder in repo (default: root)
    repo_type="model",
    revision=None,
    commit_message="Upload model",
    allow_patterns=None,
    ignore_patterns=None,
    delete_patterns=None,        # delete files matching pattern before upload
    create_pr=False,
)

# Upload single file
api.upload_file(
    path_or_fileobj="/local/file.bin",
    path_in_repo="file.bin",
    repo_id="username/my-model",
    repo_type="model",
    commit_message="Add file",
)

# Upload large folder (>100k files or >100GB, resumable)
api.upload_large_folder(
    repo_id="username/large-dataset",
    folder_path="/local/path",
    repo_type="dataset",
    allow_patterns=None,
    ignore_patterns=None,
    num_workers=None,
)
```

### File Management

```python
# List files in repo → list[str]
api.list_repo_files(repo_id, repo_type=None, revision=None)

# List repo tree → Iterable[RepoFile | RepoFolder]
api.list_repo_tree(repo_id, path_in_repo=None, recursive=False, repo_type=None, revision=None)

# Delete file/folder
api.delete_file(path_in_repo, repo_id, repo_type=None, commit_message=None)
api.delete_folder(path_in_repo, repo_id, repo_type=None, commit_message=None)
api.delete_files(repo_id, delete_patterns=["*.bin"], repo_type=None, commit_message=None)
```

### Branches & Tags

```python
api.list_repo_refs(repo_id, repo_type=None) -> GitRefs
# GitRefs has .branches and .tags (list of GitRefInfo with .name, .ref, .target_commit)

api.create_branch(repo_id, branch="dev", revision=None, repo_type=None, exist_ok=False)
api.delete_branch(repo_id, branch="dev", repo_type=None)

api.create_tag(repo_id, tag="v1.0", tag_message=None, revision=None, repo_type=None)
api.delete_tag(repo_id, tag="v1.0", repo_type=None)
```

### Commits

```python
api.list_repo_commits(repo_id, repo_type=None, revision=None) -> list[GitCommitInfo]
# GitCommitInfo: .commit_id, .title, .message, .created_at
```

### Repo Settings

```python
api.update_repo_settings(repo_id, gated="auto"|"manual"|False, private=True|False, repo_type=None)
```

### Discussions & PRs

```python
api.get_repo_discussions(repo_id, repo_type=None) -> Iterator[Discussion]
api.create_discussion(repo_id, title="Bug report", description="...", repo_type=None)
api.create_pull_request(repo_id, title="Add feature", description="...", repo_type=None)
api.comment_discussion(repo_id, discussion_num=1, comment="LGTM")
api.merge_pull_request(repo_id, discussion_num=1)
api.change_discussion_status(repo_id, discussion_num=1, new_status="closed")
```

### Like / Unlike

```python
api.like(repo_id, repo_type=None)
api.unlike(repo_id, repo_type=None)
api.list_liked_repos(user="username") -> UserLikes
```

### Collections

```python
api.list_collections(owner="username", sort="trending", limit=10) -> Iterable[Collection]
api.create_collection(title="My Models", namespace="username", private=False)
api.add_collection_item(collection_slug, item_id="username/my-model", item_type="model")
api.delete_collection(collection_slug)
```

---

## ModelScope Hub (`modelscope.hub.api`)

```python
import os
os.environ["MODELSCOPE_DOMAIN"] = "modelscope.ai"  # bare domain, no https://
from modelscope.hub.api import HubApi
api = HubApi()
api.login("ms-xxx")  # REQUIRED before authenticated calls
```

### Authentication

```python
api.login(access_token="ms-xxx")
# No whoami equivalent — login validates implicitly
```

### List Repos

```python
# Models — returns dict with keys: Models (list), total_count, page_number, page_size
result = api.list_models("username", page_number=1, page_size=50)
models = result.get("Models", [])
# Each model dict has: Name, Path, Description, Downloads, Likes, ...

# Datasets — returns dict with keys: datasets (list), total_count, page_number, page_size
result = api.list_datasets("username", page_number=1, page_size=50)
datasets = result.get("datasets", [])
# Each dataset dict has: id, display_name, description, downloads, likes, license, ...
# NOTE: models key is "Models" (uppercase), datasets key is "datasets" (lowercase)
# NOTE: model items use "Name", dataset items use "id" (e.g. "username/my-dataset")
```

### Repo Info

```python
# Model info — returns dict
api.get_model(model_id="username/my-model", revision="master")

# Dataset info — returns dict
api.get_dataset(dataset_id="username/my-dataset", revision="master")

# Generic info — returns ModelInfo | DatasetInfo
api.repo_info(repo_id, repo_type="model", revision="master")
api.model_info(repo_id, revision="master") -> ModelInfo
api.dataset_info(repo_id, revision="master") -> DatasetInfo

# Check existence (no exception)
api.repo_exists(repo_id, repo_type=None) -> bool
api.file_exists(repo_id, filename, revision=None) -> bool
```

### Create / Delete Repos

```python
# Create model
api.create_model(model_id="username/new-model", visibility=5, license="Apache License 2.0")
# visibility: 1=private, 5=public

# Create dataset (note: different param names!)
api.create_dataset(dataset_name="new-dataset", namespace="username", visibility=5,
                   license="Apache License 2.0")

# Generic create
api.create_repo(repo_id, repo_type="model", visibility="public", exist_ok=False)
# visibility: "public" or "private"

# Delete
api.delete_model(model_id="username/old-model")
api.delete_dataset(dataset_id="username/old-dataset")
api.delete_repo(repo_id, repo_type="model")

# Change visibility
api.set_repo_visibility(repo_id, repo_type="model", visibility="private")
```

### Download

```python
from modelscope.hub.snapshot_download import snapshot_download

# Download entire repo → returns local dir path
snapshot_download(
    model_id="username/my-model",  # also accepts repo_id= kwarg
    repo_type="model",                  # "model" or "dataset"
    revision=None,
    local_dir="/path/to/save",
    cache_dir=None,
    allow_patterns=["*.safetensors"],
    ignore_patterns=["*.bin"],
    max_workers=None,
    token=None,
)
# NOTE: import path is modelscope.hub.snapshot_download.snapshot_download
```

### Upload

```python
# Upload entire folder
api.upload_folder(
    repo_id="username/my-model",
    folder_path="/local/path",
    path_in_repo="",
    repo_type="model",              # "model" or "dataset"
    commit_message="Upload model",
    allow_patterns=None,
    ignore_patterns=None,
    max_workers=8,
    revision="master",
    token=None,
)
# IMPORTANT: HTTP-based, no git required
# GOTCHA: May fail with "file already exists" during commit if uploading identical files.
# Use check_repo_exists to skip repos that are already fully migrated.

# Upload single file
api.upload_file(
    path_or_fileobj="/local/file.bin",
    path_in_repo="file.bin",
    repo_id="username/my-model",
    repo_type="model",
    commit_message="Add file",
    revision="master",
)
```

### File Management

```python
# List model files → list[dict]
api.get_model_files(model_id, revision="master", root=None, recursive=False)

# List dataset files → list[dict]
api.get_dataset_files(repo_id, revision="master", root_path="/", recursive=True,
                      page_number=1, page_size=100)

# Delete files
api.delete_files(repo_id, repo_type="model", delete_patterns=["*.bin"], revision="master")
```

### Branches & Tags

```python
branches, tags = api.get_model_branches_and_tags(model_id)
# Returns (list[str], list[str])

api.list_model_revisions(model_id) -> list[str]
```

### Commits

```python
api.list_repo_commits(repo_id, repo_type="model", revision="master",
                      page_number=1, page_size=50) -> list
```

---

## Key Differences

| Feature | HuggingFace | ModelScope |
|---------|-------------|------------|
| Auth | `HfApi(token=)` or per-call `token=` | `api.login(token)` required first |
| List repos | `author="username"` kwarg | First positional arg: `"username"` |
| List result | Iterable of typed objects | dict with `Models`/`datasets` key |
| Dataset list key | N/A (iterable) | `result["datasets"]` (lowercase), items have `"id"` |
| Model list key | N/A (iterable) | `result["Models"]` (uppercase), items have `"Name"` |
| Repo exists | `api.repo_exists(repo_id, repo_type=)` | `api.repo_exists(repo_id, repo_type=)` |
| Check model exists | `api.model_info()` + try/except | `api.repo_exists()` works directly |
| Create dataset | `api.create_repo(id, repo_type="dataset")` | `api.create_dataset(name, namespace)` |
| Snapshot download | `from huggingface_hub import snapshot_download` | `from modelscope.hub.snapshot_download import snapshot_download` |
| Download param | `repo_id=` | `model_id=` (positional) or `repo_id=` (kwarg) |
| Upload folder | `api.upload_folder(repo_id=, folder_path=)` | `api.upload_folder(repo_id=, folder_path=, token=)` |
| Domain config | N/A (always huggingface.co) | `os.environ["MODELSCOPE_DOMAIN"] = "modelscope.ai"` |
| Visibility | `private=True\|False` | `visibility=1` (private) / `5` (public) |
| Spaces | Full support | No Spaces concept |
| Collections | Full support | No collections |
| Discussions/PRs | Full support | No discussions API |
