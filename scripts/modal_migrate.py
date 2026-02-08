"""Modal app for HuggingFace <-> ModelScope migration.

Usage:
    # Smoke test
    modal run scripts/modal_migrate.py::hello_world

    # Migrate HF → ModelScope (auto-detect repo type)
    modal run scripts/modal_migrate.py::main --source "username/my-model" --to ms

    # Migrate ModelScope → HF (explicit type)
    modal run scripts/modal_migrate.py::main --source "damo/text-to-video" --to hf --repo-type model

    # Custom destination name
    modal run scripts/modal_migrate.py::main --source "username/my-model" --to ms --dest "OrgName/model-v2"

    # Platform prefix instead of --to flag
    modal run scripts/modal_migrate.py::main --source "hf:username/my-model" --to ms
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import modal

# Allow importing from scripts/ when run via `modal run`
sys.path.insert(0, str(Path(__file__).resolve().parent))

app = modal.App("hf-ms-migrate")

# Minimal image: only hub clients, no torch/transformers
migrate_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "huggingface_hub",
        "modelscope",
    )
)


def _format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _strip_protocol(domain: str) -> str:
    """Strip protocol prefix from a domain string (e.g. 'https://modelscope.ai' -> 'modelscope.ai')."""
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    return domain.rstrip("/")


def _build_url(repo_id: str, platform: str, repo_type: str, ms_domain: str = "") -> str:
    """Build the web URL for a repo on the given platform.

    Defined at module level so both remote functions can use it
    (remote functions cannot import from utils.py).
    """
    if platform == "hf":
        type_prefix = {"model": "", "dataset": "datasets/", "space": "spaces/"}
        return f"https://huggingface.co/{type_prefix.get(repo_type, '')}{repo_id}"
    domain = _strip_protocol(ms_domain or "modelscope.cn")
    type_path = "datasets" if repo_type == "dataset" else "models"
    return f"https://{domain}/{type_path}/{repo_id}"


def _dir_stats(path: str) -> tuple[int, int]:
    """Count files and total size in a directory.

    Returns:
        (file_count, total_bytes)
    """
    import os

    file_count = 0
    total_bytes = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                if os.path.isfile(fp):
                    file_count += 1
                    total_bytes += os.path.getsize(fp)
            except OSError:
                file_count += 1  # count it but skip size
    return file_count, total_bytes


def _sanitize_readme_for_hf(readme_path: str) -> None:
    """Fix README.md YAML front-matter that HuggingFace rejects.

    ModelScope repos sometimes have license values (e.g. 'AFL') that are not
    in HuggingFace's allowed list.  This rewrites invalid license values to
    'other' so upload_folder validation passes.
    """
    import re

    HF_LICENSES = {
        "apache-2.0", "mit", "openrail", "bigscience-openrail-m",
        "creativeml-openrail-m", "bigscience-bloom-rail-1.0",
        "bigcode-openrail-m", "afl-3.0", "artistic-2.0", "bsl-1.0", "bsd",
        "bsd-2-clause", "bsd-3-clause", "bsd-3-clause-clear", "c-uda", "cc",
        "cc0-1.0", "cc-by-2.0", "cc-by-2.5", "cc-by-3.0", "cc-by-4.0",
        "cc-by-sa-3.0", "cc-by-sa-4.0", "cc-by-nc-2.0", "cc-by-nc-3.0",
        "cc-by-nc-4.0", "cc-by-nd-4.0", "cc-by-nc-nd-3.0", "cc-by-nc-nd-4.0",
        "cc-by-nc-sa-2.0", "cc-by-nc-sa-3.0", "cc-by-nc-sa-4.0",
        "cdla-sharing-1.0", "cdla-permissive-1.0", "cdla-permissive-2.0",
        "wtfpl", "ecl-2.0", "epl-1.0", "epl-2.0", "etalab-2.0", "eupl-1.1",
        "eupl-1.2", "agpl-3.0", "gfdl", "gpl", "gpl-2.0", "gpl-3.0", "lgpl",
        "lgpl-2.1", "lgpl-3.0", "isc", "lppl-1.3c", "ms-pl", "mpl-2.0",
        "odc-by", "odbl", "openrail++", "osl-3.0", "postgresql", "ofl-1.1",
        "ncsa", "unlicense", "zlib", "pddl", "lgpl-lr", "unknown", "other",
        "array",
    }

    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Match YAML front-matter
    m = re.match(r"^---\n(.*?\n)---\n", content, re.DOTALL)
    if not m:
        return

    front = m.group(1)
    license_match = re.search(r"^(license:\s*)(.+)$", front, re.MULTILINE)
    if not license_match:
        return

    value = license_match.group(2).strip().strip("'\"")
    if value.lower() not in HF_LICENSES:
        old_line = license_match.group(0)
        new_line = f"{license_match.group(1)}other"
        content = content.replace(old_line, new_line, 1)
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"       Sanitized README.md: license '{value}' -> 'other'")


# ---------------------------------------------------------------------------
# Remote functions (run inside Modal container)
# ---------------------------------------------------------------------------


@app.function(image=migrate_image, timeout=60)
def hello_world() -> str:
    """Smoke test: verify Modal deployment and SDK imports work."""
    import huggingface_hub
    import modelscope

    return (
        f"Modal container OK. "
        f"huggingface_hub=={huggingface_hub.__version__}, "
        f"modelscope=={modelscope.__version__}"
    )


@app.function(image=migrate_image, timeout=120)
def check_repo_exists(
    repo_id: str,
    platform: str,
    repo_type: str,
    token: str,
    ms_domain: str = "",
) -> bool:
    """Check if a repo already exists on the given platform.

    Returns:
        True if the repo exists, False otherwise.
    """
    if platform == "hf":
        from huggingface_hub import HfApi
        from huggingface_hub.utils import RepositoryNotFoundError, GatedRepoError

        api = HfApi(token=token)
        try:
            if repo_type == "dataset":
                api.dataset_info(repo_id)
            elif repo_type == "space":
                api.space_info(repo_id)
            else:
                api.model_info(repo_id)
            return True
        except RepositoryNotFoundError:
            return False
        except GatedRepoError:
            return True  # repo exists but is gated
        # All other exceptions (network, auth, rate limit) propagate to caller

    elif platform == "ms":
        import os
        if ms_domain:
            os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)

        from modelscope.hub.api import HubApi

        api = HubApi()
        api.login(token)
        # ModelScope API doesn't support repo_type "space" — fall back to
        # checking as "model" since spaces are uploaded as model repos on MS.
        check_type = "model" if repo_type == "space" else repo_type
        return api.repo_exists(repo_id=repo_id, repo_type=check_type, token=token)

    else:
        raise ValueError(f"Unknown platform: '{platform}'. Expected 'hf' or 'ms'.")


@app.function(image=migrate_image, timeout=120)
def detect_repo_type(repo_id: str, platform: str, token: str, ms_domain: str = "") -> str:
    """Auto-detect whether a repo is a model, dataset, or space.

    For HuggingFace: tries model, dataset, then space.
    For ModelScope: tries model, then dataset; raises ValueError if neither matches.

    Returns:
        "model", "dataset", or "space"
    """
    if platform == "hf":
        from huggingface_hub import HfApi
        from huggingface_hub.utils import RepositoryNotFoundError

        api = HfApi(token=token)
        last_error = None

        for type_name, info_fn in [("model", api.model_info), ("dataset", api.dataset_info), ("space", api.space_info)]:
            try:
                info_fn(repo_id)
                return type_name
            except RepositoryNotFoundError:
                continue
            except Exception as e:
                last_error = e
                continue

        if last_error is not None:
            raise RuntimeError(
                f"Could not detect repo type for '{repo_id}' on HuggingFace. "
                f"The API returned an error (not a 404): {last_error}"
            ) from last_error

        raise ValueError(f"Repo '{repo_id}' not found on HuggingFace as model, dataset, or space")

    elif platform == "ms":
        import os
        if ms_domain:
            os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)

        from modelscope.hub.api import HubApi

        api = HubApi()
        api.login(token)

        last_error = None

        try:
            api.get_model(repo_id)
            return "model"
        except Exception as e:
            if "not found" in str(e).lower() or "404" in str(e):
                pass  # genuinely not a model, try dataset
            else:
                last_error = e

        try:
            api.get_dataset(repo_id)
            return "dataset"
        except Exception as e:
            if "not found" in str(e).lower() or "404" in str(e):
                pass
            else:
                last_error = e

        if last_error is not None:
            raise RuntimeError(
                f"Could not detect repo type for '{repo_id}' on ModelScope. "
                f"The API returned an error (not a 404): {last_error}"
            ) from last_error

        # ModelScope has no reliable type detection beyond model/dataset
        raise ValueError(
            f"Could not detect repo type for '{repo_id}' on ModelScope. "
            f"Please specify --repo-type explicitly (model, dataset, or space)."
        )

    raise ValueError(f"Unknown platform: '{platform}'. Expected 'hf' or 'ms'.")


@app.function(image=migrate_image, timeout=3600)
def migrate_hf_to_ms(
    hf_repo_id: str,
    ms_repo_id: str,
    repo_type: str,
    hf_token: str,
    ms_token: str,
    ms_domain: str = "",
) -> dict:
    """Download repo from HuggingFace and upload to ModelScope.

    Returns:
        Dict with status, url, file_count, total_size, and duration.
    """
    import os
    import shutil
    import tempfile
    import time as _time

    if ms_domain:
        os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)

    from huggingface_hub import snapshot_download
    from modelscope.hub.api import HubApi

    start = _time.time()
    work_dir = tempfile.mkdtemp(prefix="hf_ms_migrate_")

    try:
        download_dir = os.path.join(work_dir, "download")

        # Step 1: Download from HuggingFace
        print(f"[1/3] Downloading {hf_repo_id} ({repo_type}) from HuggingFace...")
        dl_start = _time.time()
        local_dir = snapshot_download(
            repo_id=hf_repo_id,
            repo_type=repo_type,
            token=hf_token,
            local_dir=download_dir,
        )
        dl_time = _time.time() - dl_start

        file_count, total_bytes = _dir_stats(local_dir)
        print(f"       Downloaded {file_count} files ({_format_size(total_bytes)}) in {_format_duration(dl_time)}")

        # Step 2: Create repo on ModelScope if needed
        print(f"[2/3] Ensuring ModelScope repo exists: {ms_repo_id}...")
        api = HubApi()
        api.login(ms_token)
        if not api.repo_exists(repo_id=ms_repo_id, repo_type=repo_type, token=ms_token):
            if repo_type == "dataset":
                if "/" not in ms_repo_id:
                    raise ValueError(f"Invalid ModelScope repo ID: '{ms_repo_id}'. Expected format: 'namespace/name'")
                namespace, name = ms_repo_id.split("/", 1)
                api.create_dataset(
                    dataset_name=name,
                    namespace=namespace,
                    visibility=5,  # public
                )
            else:
                api.create_model(
                    model_id=ms_repo_id,
                    visibility=5,  # public
                )
            print("       Created new repo")
        else:
            print("       Repo already exists, will update")

        # Step 3: Upload via HTTP API (no git required)
        print(f"[3/3] Uploading {file_count} files ({_format_size(total_bytes)}) to ModelScope as {ms_repo_id}...")
        ul_start = _time.time()
        upload_kwargs = {
            "repo_id": ms_repo_id,
            "folder_path": local_dir,
            "token": ms_token,
        }
        if repo_type == "dataset":
            upload_kwargs["repo_type"] = "dataset"
        api.upload_folder(**upload_kwargs)
        ul_time = _time.time() - ul_start

        total_time = _time.time() - start
        url = _build_url(ms_repo_id, "ms", repo_type, ms_domain)
        print(f"       Uploaded in {_format_duration(ul_time)}")
        print(f"       Total: {_format_duration(total_time)}")
        print(f"       URL: {url}")

        return {
            "status": "success",
            "url": url,
            "file_count": file_count,
            "total_size": _format_size(total_bytes),
            "duration": _format_duration(total_time),
        }

    except Exception as e:
        import traceback
        total_time = _time.time() - start
        tb = traceback.format_exc()
        print(f"ERROR after {_format_duration(total_time)}: {e}")
        print(f"Traceback:\n{tb}")
        return {
            "status": "error",
            "error": str(e),
            "traceback": tb,
            "duration": _format_duration(total_time),
        }

    finally:
        try:
            shutil.rmtree(work_dir)
        except OSError as e:
            print(f"WARNING: Failed to clean up {work_dir}: {e}")


@app.function(image=migrate_image, timeout=3600)
def migrate_ms_to_hf(
    ms_repo_id: str,
    hf_repo_id: str,
    repo_type: str,
    hf_token: str,
    ms_token: str,
    ms_domain: str = "",
) -> dict:
    """Download repo from ModelScope and upload to HuggingFace.

    Returns:
        Dict with status, url, file_count, total_size, and duration.
    """
    import os
    import shutil
    import tempfile
    import time as _time

    if ms_domain:
        os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)

    from huggingface_hub import HfApi
    from modelscope.hub.snapshot_download import snapshot_download as ms_snapshot_download

    start = _time.time()
    work_dir = tempfile.mkdtemp(prefix="ms_hf_migrate_")

    try:
        # Step 1: Download from ModelScope
        # NOTE: repo_type is passed for datasets below. Only model downloads
        # are well-tested for MS->HF direction.
        print(f"[1/2] Downloading {ms_repo_id} ({repo_type}) from ModelScope...")
        dl_start = _time.time()
        dl_kwargs = {"model_id": ms_repo_id, "cache_dir": work_dir}
        if repo_type == "dataset":
            dl_kwargs["repo_type"] = "dataset"
        local_dir = ms_snapshot_download(**dl_kwargs)
        dl_time = _time.time() - dl_start

        file_count, total_bytes = _dir_stats(local_dir)
        print(f"       Downloaded {file_count} files ({_format_size(total_bytes)}) in {_format_duration(dl_time)}")

        # Sanitize README.md metadata for HuggingFace compatibility
        readme_path = os.path.join(local_dir, "README.md")
        if os.path.exists(readme_path):
            _sanitize_readme_for_hf(readme_path)

        # Step 2: Upload to HuggingFace
        print(f"[2/2] Uploading {file_count} files ({_format_size(total_bytes)}) to HuggingFace as {hf_repo_id}...")
        ul_start = _time.time()
        api = HfApi(token=hf_token)
        api.create_repo(repo_id=hf_repo_id, repo_type=repo_type, exist_ok=True)
        api.upload_folder(
            folder_path=local_dir,
            repo_id=hf_repo_id,
            repo_type=repo_type,
            commit_message=f"Migrated from ModelScope: {ms_repo_id}",
        )
        ul_time = _time.time() - ul_start

        total_time = _time.time() - start
        url = _build_url(hf_repo_id, "hf", repo_type)
        print(f"       Uploaded in {_format_duration(ul_time)}")
        print(f"       Total: {_format_duration(total_time)}")
        print(f"       URL: {url}")

        return {
            "status": "success",
            "url": url,
            "file_count": file_count,
            "total_size": _format_size(total_bytes),
            "duration": _format_duration(total_time),
        }

    except Exception as e:
        import traceback
        total_time = _time.time() - start
        tb = traceback.format_exc()
        print(f"ERROR after {_format_duration(total_time)}: {e}")
        print(f"Traceback:\n{tb}")
        return {
            "status": "error",
            "error": str(e),
            "traceback": tb,
            "duration": _format_duration(total_time),
        }

    finally:
        try:
            shutil.rmtree(work_dir)
        except OSError as e:
            print(f"WARNING: Failed to clean up {work_dir}: {e}")


# ---------------------------------------------------------------------------
# Local entrypoint (runs on your machine, orchestrates remote functions)
# ---------------------------------------------------------------------------


@app.local_entrypoint()
def main(
    source: str,
    to: str = "",
    repo_type: str = "",
    dest: str = "",
):
    """Migrate a repo between HuggingFace and ModelScope via Modal.

    Args:
        source: Source repo ID. Supports platform prefix: "hf:user/repo" or "ms:user/repo"
        to: Destination platform — "hf" or "ms". Required if source has no prefix.
        repo_type: "model", "dataset", or "space". Auto-detected if omitted.
        dest: Custom destination repo ID. Defaults to same as source.
    """
    # Import utils here — only the local entrypoint needs them,
    # and they're not available inside the Modal container.
    from utils import build_url, detect_direction, get_env_token, get_ms_domain, parse_repo_id

    print()
    print("=" * 50)
    print("  HF-Modal-ModelScope Migration")
    print("=" * 50)

    try:
        # 1. Parse source
        repo_id, source_platform = parse_repo_id(source)

        # 2. Determine direction
        src_plat, dst_plat = detect_direction(source_platform, to or None)

        # 3. Read tokens and domain config
        print()
        print("Checking credentials...")
        hf_token = get_env_token("HF_TOKEN")
        ms_token = get_env_token("MODELSCOPE_TOKEN")
        ms_domain = get_ms_domain()
        print("  HF_TOKEN: OK")
        print("  MODELSCOPE_TOKEN: OK")
        print(f"  MODELSCOPE_DOMAIN: {ms_domain}")

        # 4. Auto-detect repo type if not provided
        if not repo_type:
            print()
            print(f"Auto-detecting repo type for {repo_id}...")
            detect_token = hf_token if src_plat == "hf" else ms_token
            repo_type = detect_repo_type.remote(repo_id, src_plat, detect_token, ms_domain)
            print(f"  Detected: {repo_type}")

        # 5. Determine destination repo ID
        dest_repo_id = dest if dest else repo_id

        # 6. Summary
        src_name = "HuggingFace" if src_plat == "hf" else "ModelScope"
        dst_name = "HuggingFace" if dst_plat == "hf" else "ModelScope"
        src_url = build_url(repo_id, src_plat, repo_type)
        dst_url = build_url(dest_repo_id, dst_plat, repo_type)

        print()
        print("-" * 50)
        print(f"  Source:      {src_name} / {repo_id} ({repo_type})")
        print(f"               {src_url}")
        print(f"  Destination: {dst_name} / {dest_repo_id}")
        print(f"               {dst_url}")
        print("-" * 50)
        print()

        # 7. Check if destination repo already exists
        dest_token = ms_token if dst_plat == "ms" else hf_token
        dest_exists = check_repo_exists.remote(dest_repo_id, dst_plat, repo_type, dest_token, ms_domain)
        if dest_exists:
            print(f"  NOTE: {dest_repo_id} already exists on {dst_name}. Files will be updated/overwritten.")
            print()

        # 8. Run migration
        start = time.time()
        print("Starting migration...")
        print()

        if src_plat == "hf" and dst_plat == "ms":
            result = migrate_hf_to_ms.remote(
                hf_repo_id=repo_id,
                ms_repo_id=dest_repo_id,
                repo_type=repo_type,
                hf_token=hf_token,
                ms_token=ms_token,
                ms_domain=ms_domain,
            )
        elif src_plat == "ms" and dst_plat == "hf":
            result = migrate_ms_to_hf.remote(
                ms_repo_id=repo_id,
                hf_repo_id=dest_repo_id,
                repo_type=repo_type,
                hf_token=hf_token,
                ms_token=ms_token,
                ms_domain=ms_domain,
            )
        else:
            print(f"ERROR: Unsupported direction {src_plat} -> {dst_plat}")
            return

        # 9. Report
        print()
        print("=" * 50)
        if result["status"] == "success":
            print("  Migration complete!")
            print(f"  URL:      {result['url']}")
            print(f"  Files:    {result['file_count']}")
            print(f"  Size:     {result['total_size']}")
            print(f"  Duration: {result['duration']}")
        else:
            print("  Migration FAILED")
            print(f"  Error:    {result.get('error', 'Unknown error')}")
            print(f"  Duration: {result.get('duration', 'N/A')}")
            if result.get("traceback"):
                print()
                print("  Remote traceback:")
                for tb_line in result["traceback"].splitlines():
                    print(f"    {tb_line}")
            print()
            print("  Troubleshooting:")
            error_msg = result.get("error", "").lower()
            if "token" in error_msg or "auth" in error_msg or "401" in error_msg:
                print("  - Check your tokens: python scripts/validate_tokens.py")
            elif "not found" in error_msg or "404" in error_msg:
                print("  - Verify the repo ID exists on the source platform")
            elif "timeout" in error_msg:
                print("  - Repo may be too large. Try with --repo-type to skip auto-detect")
            else:
                print("  - Check Modal status: modal token verify")
                print("  - Re-run token validation: python scripts/validate_tokens.py")
        print("=" * 50)

    except ValueError as e:
        print()
        print(f"ERROR: {e}")
        print()
        print("Usage: modal run scripts/modal_migrate.py::main --source <repo> --to <hf|ms>")
        print("  See README.md for examples.")

    except Exception as e:
        import traceback
        print()
        print(f"Unexpected error: {e}")
        print(traceback.format_exc())
        print("If this persists, check:")
        print("  - Modal account: modal token verify")
        print("  - Platform tokens: python scripts/validate_tokens.py")


@app.local_entrypoint()
def batch(
    source: str,
    to: str = "",
    repo_type: str = "model",
):
    """Migrate multiple repos in parallel using multiple Modal containers.

    Args:
        source: Comma-separated repo IDs (e.g., "user/repo1,user/repo2,user/repo3").
            Can also use platform prefixes (e.g., "hf:user/repo1,hf:user/repo2").
        to: Destination platform — "hf" or "ms". Optional if repos use platform prefixes.
        repo_type: "model", "dataset", or "space". Applied to all repos (default: model).
    """
    from utils import detect_direction, get_env_token, get_ms_domain, parse_repo_id

    try:
        repo_list = [r.strip() for r in source.split(",") if r.strip()]
        if not repo_list:
            print("ERROR: No repos provided. Use comma-separated list.")
            return

        print()
        print("=" * 60)
        print(f"  HF2MS Batch Migration — {len(repo_list)} repos")
        print("=" * 60)

        # Credentials
        print()
        print("Checking credentials...")
        hf_token = get_env_token("HF_TOKEN")
        ms_token = get_env_token("MODELSCOPE_TOKEN")
        ms_domain = get_ms_domain()
        print("  HF_TOKEN: OK")
        print("  MODELSCOPE_TOKEN: OK")
        print(f"  MODELSCOPE_DOMAIN: {ms_domain}")

        # Parse all repos and determine direction
        jobs = []
        for raw in repo_list:
            repo_id, source_platform = parse_repo_id(raw)
            to_flag = to if to else None
            src_plat, dst_plat = detect_direction(source_platform, to_flag)
            jobs.append((repo_id, src_plat, dst_plat))

        # Print plan
        print()
        print(f"  Repos ({repo_type}):")
        for repo_id, src_plat, dst_plat in jobs:
            src_name = "HF" if src_plat == "hf" else "MS"
            dst_name = "HF" if dst_plat == "hf" else "MS"
            print(f"    {src_name} -> {dst_name}  {repo_id}")
        print()

        # Pre-check: which repos already exist on destination?
        print("Checking destination repos for existing copies...")
        check_args = []
        for repo_id, src_plat, dst_plat in jobs:
            dest_token = ms_token if dst_plat == "ms" else hf_token
            check_args.append((repo_id, dst_plat, repo_type, dest_token, ms_domain))

        existing = set()
        try:
            for i, exists in enumerate(check_repo_exists.starmap(check_args)):
                repo_id = jobs[i][0]
                if exists:
                    existing.add(repo_id)
                    print(f"  SKIP {repo_id} — already exists on destination")
        except Exception as e:
            error_msg = str(e).lower()
            if "auth" in error_msg or "token" in error_msg or "401" in error_msg or "403" in error_msg:
                print(f"  ERROR: Pre-check failed due to authentication issue: {e}")
                print("  Cannot proceed without valid credentials. Aborting batch.")
                return
            print(f"  WARNING: Pre-check failed ({e}). Proceeding without skipping existing repos.")

        if existing:
            print(f"  Skipping {len(existing)} existing repo(s)")
        else:
            print("  No existing repos found, migrating all")
        print()

        # Build starmap args based on direction (excluding existing)
        start = time.time()
        hf_to_ms_args = []
        ms_to_hf_args = []
        for repo_id, src_plat, dst_plat in jobs:
            if repo_id in existing:
                continue
            if src_plat == "hf" and dst_plat == "ms":
                hf_to_ms_args.append((repo_id, repo_id, repo_type, hf_token, ms_token, ms_domain))
            elif src_plat == "ms" and dst_plat == "hf":
                ms_to_hf_args.append((repo_id, repo_id, repo_type, hf_token, ms_token, ms_domain))

        total_to_migrate = len(hf_to_ms_args) + len(ms_to_hf_args)
        if total_to_migrate == 0:
            print("All repos already exist on destination. Nothing to migrate.")
            return

        print(f"Launching {total_to_migrate} parallel containers...")
        print()

        results = []

        def _run_starmap(fn, args, label, results):
            """Fan out jobs via starmap, tracking completed and in-flight repos."""
            if not args:
                return
            try:
                for i, result in enumerate(fn.starmap(args)):
                    repo_id = args[i][0]
                    status = result.get("status", "error")
                    if status == "success":
                        print(f"  OK  {repo_id} — {result['file_count']} files, {result['total_size']}, {result['duration']}")
                    else:
                        print(f"  FAIL {repo_id} — {result.get('error', 'Unknown')}")
                    results.append((repo_id, result))
            except Exception as e:
                completed_ids = {r[0] for r in results}
                in_flight = [a[0] for a in args if a[0] not in completed_ids]
                print(f"\n  BATCH ERROR ({label}): {e}")
                print(f"  {len(results)} repos completed before failure.")
                if in_flight:
                    print(f"  Status unknown for: {', '.join(in_flight)}")

        _run_starmap(migrate_hf_to_ms, hf_to_ms_args, "HF->MS", results)
        _run_starmap(migrate_ms_to_hf, ms_to_hf_args, "MS->HF", results)

        # Summary
        total_time = time.time() - start
        succeeded = sum(1 for _, r in results if r.get("status") == "success")
        failed = len(results) - succeeded
        skipped = len(existing)

        print()
        print("=" * 60)
        print(f"  Batch complete in {_format_duration(total_time)}")
        print(f"  Succeeded: {succeeded}/{len(results)}")
        if skipped:
            print(f"  Skipped:   {skipped} (already exist)")
        if failed:
            print(f"  Failed:    {failed}")
            for repo_id, r in results:
                if r.get("status") != "success":
                    print(f"    - {repo_id}: {r.get('error', 'Unknown')}")
        print("=" * 60)

    except ValueError as e:
        print()
        print(f"ERROR: {e}")
        print()
        print("Usage: modal run scripts/modal_migrate.py::batch --source \"repo1,repo2\" --to <hf|ms> --repo-type <type>")
        print("  See README.md for examples.")

    except Exception as e:
        import traceback
        print()
        print(f"Unexpected error: {e}")
        print(traceback.format_exc())
        print("If this persists, check:")
        print("  - Modal account: modal token verify")
        print("  - Platform tokens: python scripts/validate_tokens.py")
