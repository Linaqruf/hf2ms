"""Modal app for HuggingFace <-> ModelScope migration.

Usage:
    # Smoke test
    modal run scripts/modal_migrate.py::hello_world

    # Migrate HF → ModelScope (auto-detect repo type)
    modal run scripts/modal_migrate.py --source "Linaqruf/animagine-xl-3.1" --to ms

    # Migrate ModelScope → HF (explicit type)
    modal run scripts/modal_migrate.py --source "damo/text-to-video" --to hf --repo-type model

    # Custom destination name
    modal run scripts/modal_migrate.py --source "Linaqruf/model" --to ms --dest "Linaqruf/model-copy"

    # Platform prefix instead of --to flag
    modal run scripts/modal_migrate.py --source "hf:Linaqruf/model" --to ms
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
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


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
            if os.path.isfile(fp):
                file_count += 1
                total_bytes += os.path.getsize(fp)
    return file_count, total_bytes


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

        api = HfApi(token=token)
        try:
            if repo_type == "dataset":
                api.dataset_info(repo_id)
            elif repo_type == "space":
                api.space_info(repo_id)
            else:
                api.model_info(repo_id)
            return True
        except Exception:
            return False

    elif platform == "ms":
        import os
        if ms_domain:
            os.environ["MODELSCOPE_DOMAIN"] = ms_domain

        from modelscope.hub.api import HubApi

        api = HubApi()
        api.login(token)
        return api.repo_exists(repo_id=repo_id, repo_type=repo_type, token=token)

    return False


@app.function(image=migrate_image, timeout=120)
def detect_repo_type(repo_id: str, platform: str, token: str, ms_domain: str = "") -> str:
    """Auto-detect whether a repo is a model, dataset, or space.

    Tries model first, then dataset, then falls back to space (HF only).

    Returns:
        "model", "dataset", or "space"
    """
    if platform == "hf":
        from huggingface_hub import HfApi

        api = HfApi(token=token)

        try:
            api.model_info(repo_id)
            return "model"
        except Exception:
            pass

        try:
            api.dataset_info(repo_id)
            return "dataset"
        except Exception:
            pass

        try:
            api.space_info(repo_id)
            return "space"
        except Exception:
            pass

        raise ValueError(f"Repo '{repo_id}' not found on HuggingFace as model, dataset, or space")

    elif platform == "ms":
        import os
        if ms_domain:
            os.environ["MODELSCOPE_DOMAIN"] = ms_domain

        from modelscope.hub.api import HubApi

        api = HubApi()
        api.login(token)

        try:
            api.get_model(repo_id)
            return "model"
        except Exception:
            pass

        # Fallback: assume model (ModelScope's primary type)
        return "model"

    raise ValueError(f"Unknown platform: {platform}")


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
        os.environ["MODELSCOPE_DOMAIN"] = ms_domain

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
                parts = ms_repo_id.split("/")
                api.create_dataset(
                    dataset_name=parts[1],
                    namespace=parts[0],
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
        domain = ms_domain or "modelscope.cn"
        # Strip protocol if present (SDK expects bare domain)
        for _p in ("https://", "http://"):
            if domain.startswith(_p):
                domain = domain[len(_p):]
        type_path = "datasets" if repo_type == "dataset" else "models"
        url = f"https://{domain.rstrip('/')}/{type_path}/{ms_repo_id}"
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
        total_time = _time.time() - start
        print(f"ERROR after {_format_duration(total_time)}: {e}")
        return {
            "status": "error",
            "error": str(e),
            "duration": _format_duration(total_time),
        }

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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
        os.environ["MODELSCOPE_DOMAIN"] = ms_domain

    from huggingface_hub import HfApi
    from modelscope.hub.snapshot_download import snapshot_download as ms_snapshot_download

    start = _time.time()
    work_dir = tempfile.mkdtemp(prefix="ms_hf_migrate_")

    try:
        # Step 1: Download from ModelScope
        print(f"[1/2] Downloading {ms_repo_id} ({repo_type}) from ModelScope...")
        dl_start = _time.time()
        local_dir = ms_snapshot_download(
            model_id=ms_repo_id,
            cache_dir=work_dir,
        )
        dl_time = _time.time() - dl_start

        file_count, total_bytes = _dir_stats(local_dir)
        print(f"       Downloaded {file_count} files ({_format_size(total_bytes)}) in {_format_duration(dl_time)}")

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
        type_prefix = {"model": "", "dataset": "datasets/", "space": "spaces/"}
        url = f"https://huggingface.co/{type_prefix.get(repo_type, '')}{hf_repo_id}"
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
        total_time = _time.time() - start
        print(f"ERROR after {_format_duration(total_time)}: {e}")
        return {
            "status": "error",
            "error": str(e),
            "duration": _format_duration(total_time),
        }

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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
        to_flag = to if to else None
        src_plat, dst_plat = detect_direction(source_platform, to_flag)

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
        print("Usage: modal run scripts/modal_migrate.py --source <repo> --to <hf|ms>")
        print("  See README.md for examples.")

    except Exception as e:
        print()
        print(f"Unexpected error: {e}")
        print()
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
        source: Comma-separated repo IDs (e.g., "user/repo1,user/repo2,user/repo3")
        to: Destination platform — "hf" or "ms".
        repo_type: "model", "dataset", or "space". Applied to all repos.
    """
    from utils import get_env_token, get_ms_domain, parse_repo_id, detect_direction

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
    for i, exists in enumerate(check_repo_exists.starmap(check_args)):
        repo_id = jobs[i][0]
        if exists:
            existing.add(repo_id)
            print(f"  SKIP {repo_id} — already exists on destination")

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

    # Fan out HF->MS jobs
    if hf_to_ms_args:
        for result in migrate_hf_to_ms.starmap(hf_to_ms_args):
            repo_id = hf_to_ms_args[len(results)][0]
            status = result.get("status", "error")
            if status == "success":
                print(f"  OK  {repo_id} — {result['file_count']} files, {result['total_size']}, {result['duration']}")
            else:
                print(f"  FAIL {repo_id} — {result.get('error', 'Unknown')}")
            results.append((repo_id, result))

    # Fan out MS->HF jobs
    if ms_to_hf_args:
        offset = len(results)
        for result in migrate_ms_to_hf.starmap(ms_to_hf_args):
            repo_id = ms_to_hf_args[len(results) - offset][0]
            status = result.get("status", "error")
            if status == "success":
                print(f"  OK  {repo_id} — {result['file_count']} files, {result['total_size']}, {result['duration']}")
            else:
                print(f"  FAIL {repo_id} — {result.get('error', 'Unknown')}")
            results.append((repo_id, result))

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
