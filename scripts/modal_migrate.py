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

    # Platform prefix (infers direction — no --to needed)
    modal run scripts/modal_migrate.py::main --source "hf:username/my-model"

    # Force git clone instead of Hub API
    modal run scripts/modal_migrate.py::main --source "username/my-model" --to ms --use-git

    # Parallel chunked migration (large repos, multiple containers)
    modal run scripts/modal_migrate.py::main --source "org/large-dataset" --to ms --parallel
    modal run scripts/modal_migrate.py::main --source "org/large-dataset" --to ms --parallel --chunk-size 30

    # Batch (one container per repo)
    modal run scripts/modal_migrate.py::batch --source "repo1,repo2,repo3" --to ms --repo-type model

    # Fire & forget (detached — continues in cloud after local exit)
    modal run --detach scripts/modal_migrate.py::main --source "username/my-model" --to ms
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
# git + git-lfs included to support --use-git bypass for storage-locked orgs
migrate_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "git-lfs")
    .run_commands("git lfs install")
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

    Defined at module level so all remote functions can use it
    (remote functions cannot import from utils.py).
    """
    if platform == "hf":
        type_prefix = {"model": "", "dataset": "datasets/", "space": "spaces/"}
        return f"https://huggingface.co/{type_prefix.get(repo_type, '')}{repo_id}"
    domain = _strip_protocol(ms_domain or "modelscope.cn")
    type_path = "datasets" if repo_type == "dataset" else "models"
    return f"https://{domain}/{type_path}/{repo_id}"


def _dir_stats(path: str, exclude_dirs: set[str] | None = None) -> tuple[int, int]:
    """Count files and total size in a directory.

    Args:
        path: Directory to scan.
        exclude_dirs: Directory names to skip (e.g. {".git"}).

    Returns:
        (file_count, total_bytes)
    """
    import os

    file_count = 0
    total_bytes = 0
    for root, dirs, files in os.walk(path):
        if exclude_dirs:
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for f in files:
            fp = os.path.join(root, f)
            try:
                if os.path.isfile(fp):
                    file_count += 1
                    total_bytes += os.path.getsize(fp)
            except OSError:
                file_count += 1  # count it but skip size
    return file_count, total_bytes


def _parse_lfs_pointer_full(filepath: str) -> tuple[int | None, str | None]:
    """Read a git-lfs pointer file and extract size and SHA256.

    Returns (size_bytes, sha256_hex) or (None, None) if not a valid pointer.
    """
    size = None
    sha256 = None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read(1024)
        for line in content.splitlines():
            if line.startswith("size "):
                size = int(line.split(" ", 1)[1])
            elif line.startswith("oid sha256:"):
                sha256 = line.split(":", 1)[1].strip()
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    return size, sha256


def _build_chunks(
    file_manifest: list[dict],
    chunk_size_bytes: int,
) -> list[list[dict]]:
    """Split file manifest into chunks of approximately chunk_size_bytes.

    Chunk 0 always contains ALL non-LFS files (metadata, READMEs, configs).
    LFS files are sorted largest-first, then assigned using next-fit decreasing:
    each file goes into the last chunk if it fits, otherwise starts a new chunk.
    A single file larger than chunk_size_bytes gets its own chunk.
    """
    non_lfs = [f for f in file_manifest if not f["is_lfs"]]
    lfs = sorted(
        [f for f in file_manifest if f["is_lfs"]],
        key=lambda x: x["size"],
        reverse=True,
    )

    # Chunk 0 starts with all non-LFS files
    chunks: list[list[dict]] = [list(non_lfs)]

    for f in lfs:
        # Try to fit into the last chunk
        last_size = sum(x["size"] for x in chunks[-1])
        if last_size + f["size"] <= chunk_size_bytes and chunks[-1]:
            chunks[-1].append(f)
        else:
            chunks.append([f])

    # Remove empty chunk 0 if there were no non-LFS files
    if not chunks[0]:
        chunks.pop(0)

    return chunks


def _sanitize_readme_for_hf(readme_path: str) -> None:
    """Best-effort fix for README.md YAML front-matter that HuggingFace rejects.

    ModelScope repos sometimes have license values (e.g. 'proprietary') that
    are not in HuggingFace's allowed list, or use wrong casing (e.g.
    'Apache-2.0' instead of 'apache-2.0'). This normalizes to lowercase and
    rewrites invalid values to 'other' so upload_folder validation passes.

    This is best-effort — failures are logged but never abort the migration.
    """
    import re

    # From HuggingFace's validated license list (2025-01). HF requires exact
    # lowercase match. "array" is an HF-internal value for multi-license repos.
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

    try:
        with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        print(f"       WARNING: Could not read README.md for sanitization: {e}")
        return

    # Match YAML front-matter
    m = re.match(r"^---\n(.*?\n)---\n", content, re.DOTALL)
    if not m:
        return

    front = m.group(1)
    license_match = re.search(r"^(license:\s*)(.+)$", front, re.MULTILINE)
    if not license_match:
        return

    value = license_match.group(2).strip().strip("'\"")
    normalized = value.lower()

    if normalized in HF_LICENSES and value == normalized:
        return  # already valid and correctly cased

    # Determine replacement: normalize casing if valid, otherwise 'other'
    replacement = normalized if normalized in HF_LICENSES else "other"
    old_line = license_match.group(0)
    new_line = f"{license_match.group(1)}{replacement}"
    new_front = front.replace(old_line, new_line, 1)
    content = "---\n" + new_front + "---\n" + content[m.end():]

    try:
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"       Sanitized README.md: license '{value}' -> '{replacement}'")
    except OSError as e:
        print(f"       WARNING: Could not write sanitized README.md: {e}")


def _git_clone_hf(hf_repo_id: str, repo_type: str, hf_token: str, work_dir: str) -> tuple[str, int, int]:
    """Git clone a HuggingFace repo with LFS files.

    Uses git clone + git lfs pull instead of the HF Hub API. This bypasses
    the 403 Forbidden error when an org has exceeded its private storage limit
    (HF locks API downloads but git-based access still works).

    Returns:
        (clone_dir, file_count, total_bytes)
    """
    import os
    import shutil
    import subprocess
    import time as _time

    type_prefix = {"dataset": "datasets/", "space": "spaces/"}.get(repo_type, "")
    clone_url = f"https://user:{hf_token}@huggingface.co/{type_prefix}{hf_repo_id}"
    clone_dir = os.path.join(work_dir, "repo")

    print("       Git cloning (structure only)...")
    dl_start = _time.time()
    env = os.environ.copy()
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    proc = subprocess.run(
        ["git", "clone", "--depth=1", clone_url, clone_dir],
        env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.replace(hf_token, "***")
        raise RuntimeError(f"git clone failed: {stderr}")

    clone_time = _time.time() - dl_start
    print(f"       Cloned structure in {_format_duration(clone_time)}")

    print("       Pulling LFS files (this may take a while for large repos)...", flush=True)
    lfs_start = _time.time()
    proc = subprocess.Popen(
        ["git", "lfs", "pull"],
        cwd=clone_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Git LFS suppresses progress output when stdout is a pipe (non-TTY).
    # Monitor directory size in a background thread to show download progress.
    import threading

    stop_monitor = threading.Event()
    PROGRESS_INTERVAL = 10  # seconds between progress prints

    def _monitor_dir_size():
        last_size = 0
        _logged_error = False
        while not stop_monitor.is_set():
            stop_monitor.wait(PROGRESS_INTERVAL)
            if stop_monitor.is_set():
                break
            try:
                _, cur_size = _dir_stats(clone_dir, exclude_dirs={".git"})
            except Exception as _me:
                if not _logged_error:
                    print(f"       WARNING: Progress monitor error: {_me}")
                    _logged_error = True
                continue
            elapsed = _time.time() - lfs_start
            if cur_size > last_size:
                speed = (cur_size - last_size) / PROGRESS_INTERVAL
                print(
                    f"       [{_format_duration(elapsed)}] "
                    f"Downloaded {_format_size(cur_size)} "
                    f"({_format_size(int(speed))}/s)",
                    flush=True,
                )
                last_size = cur_size

    monitor = threading.Thread(target=_monitor_dir_size, daemon=True)
    monitor.start()

    # Capture any text output (errors, warnings)
    lfs_output = []
    for line in proc.stdout:
        clean = line.rstrip().replace(hf_token, "***")
        if clean:
            lfs_output.append(clean)
    proc.wait()
    stop_monitor.set()
    monitor.join(timeout=2)

    if proc.returncode != 0:
        raise RuntimeError(f"git lfs pull failed (exit {proc.returncode}): {' '.join(lfs_output[-5:])}")

    lfs_time = _time.time() - lfs_start
    print(f"       LFS pull done in {_format_duration(lfs_time)}")

    # Remove .git directory (not needed for upload, saves disk space)
    git_dir = os.path.join(clone_dir, ".git")
    if os.path.isdir(git_dir):
        shutil.rmtree(git_dir)

    file_count, total_bytes = _dir_stats(clone_dir)
    dl_total = _time.time() - dl_start
    print(f"       Downloaded {file_count} files ({_format_size(total_bytes)}) in {_format_duration(dl_total)}")

    return clone_dir, file_count, total_bytes


def _get_hf_sha256(
    hf_repo_id: str,
    repo_type: str,
    hf_token: str,
) -> dict[str, str]:
    """Query HuggingFace API for SHA256 hashes of all LFS files.

    Returns {path: sha256_hex} for files that have LFS hashes.
    """
    from huggingface_hub import HfApi

    hf_api = HfApi(token=hf_token)
    try:
        if repo_type == "space":
            # space_info() does not support files_metadata — SHA256 unavailable
            print("       NOTE: SHA256 verification not available for spaces (API limitation).")
            return {}
        elif repo_type == "dataset":
            info = hf_api.dataset_info(hf_repo_id, files_metadata=True)
        else:
            info = hf_api.model_info(hf_repo_id, files_metadata=True)

        sha_map = {}
        for s in getattr(info, "siblings", []):
            if s.lfs and isinstance(s.lfs, dict):
                sha = s.lfs.get("sha256")
            elif hasattr(s.lfs, "sha256"):
                sha = s.lfs.sha256
            else:
                sha = None
            if sha:
                sha_map[s.rfilename] = sha
        return sha_map
    except Exception as e:
        print(f"       WARNING: Could not fetch SHA256 hashes from HuggingFace: {e}")
        print("       SHA256 verification will be skipped.")
        return {}


def _get_ms_sha256(
    api,
    ms_repo_id: str,
    repo_type: str,
) -> dict[str, str]:
    """Query ModelScope API for SHA256 hashes of all files.

    Returns {path: sha256_hex} for files that have SHA256 hashes.
    """
    try:
        sha_map = {}
        if repo_type == "dataset":
            page = 1
            while True:
                batch = api.get_dataset_files(
                    ms_repo_id, recursive=True,
                    page_number=page, page_size=100,
                )
                for f in batch:
                    if isinstance(f, dict) and f.get("Type") == "blob":
                        sha = f.get("Sha256", "")
                        if sha:
                            sha_map[f["Path"]] = sha
                if len(batch) < 100:
                    break
                page += 1
        else:
            raw = api.get_model_files(ms_repo_id, recursive=True)
            for f in raw:
                if isinstance(f, dict) and f.get("Type") == "blob":
                    sha = f.get("Sha256", "")
                    if sha:
                        sha_map[f.get("Path") or f.get("Name", "")] = sha
        return sha_map
    except Exception as e:
        print(f"       WARNING: Could not fetch SHA256 hashes from ModelScope: {e}")
        print("       SHA256 verification will be skipped.")
        return {}


def _verify_ms_upload(
    api,
    ms_repo_id: str,
    repo_type: str,
    expected_file_count: int,
    expected_bytes: int,
    source_sha256: dict[str, str] | None = None,
) -> dict:
    """Verify uploaded files on ModelScope match the source.

    Uses get_dataset_files / get_model_files to enumerate destination files
    and compare file count, total size, and SHA256 hashes against source.

    Note: Platform-generated files (.gitattributes, README.md) are excluded
    from SHA256 comparison as they may differ between platforms.

    Args:
        source_sha256: Optional mapping of {path: sha256_hex} from the source
                       platform. If provided, enables per-file hash verification.
    """
    # Platform-generated files that may differ between HF and MS
    PLATFORM_FILES = {".gitattributes", "README.md"}

    try:
        # Enumerate destination files via paginated API
        dest_file_map: dict[str, dict] = {}
        if repo_type == "dataset":
            page = 1
            while True:
                batch = api.get_dataset_files(
                    ms_repo_id, recursive=True,
                    page_number=page, page_size=100,
                )
                for f in batch:
                    if isinstance(f, dict) and f.get("Type") == "blob":
                        dest_file_map[f["Path"]] = {
                            "size": f.get("Size", 0),
                            "sha256": f.get("Sha256", ""),
                        }
                if len(batch) < 100:
                    break
                page += 1
        else:
            raw = api.get_model_files(ms_repo_id, recursive=True)
            for f in raw:
                if isinstance(f, dict) and f.get("Type") == "blob":
                    dest_file_map[f.get("Path") or f.get("Name", "")] = {
                        "size": f.get("Size", 0),
                        "sha256": f.get("Sha256", ""),
                    }

        dest_files = len(dest_file_map)
        dest_size = sum(v["size"] for v in dest_file_map.values())

        result = {
            "source_files": expected_file_count,
            "source_size": _format_size(expected_bytes),
            "dest_files": dest_files,
            "dest_size": _format_size(dest_size),
            "files_match": dest_files >= expected_file_count,
        }

        # SHA256 verification if source hashes are provided
        if source_sha256:
            matched = 0
            skipped = 0
            mismatched = []
            missing = []
            for path, src_sha in source_sha256.items():
                if path in PLATFORM_FILES:
                    continue
                if path not in dest_file_map:
                    missing.append(path)
                    continue
                dst_sha = dest_file_map[path].get("sha256", "")
                if src_sha and dst_sha and src_sha == dst_sha:
                    matched += 1
                elif src_sha and dst_sha and src_sha != dst_sha:
                    mismatched.append(path)
                else:
                    skipped += 1  # one or both hashes missing, cannot verify

            result["sha256_matched"] = matched
            result["sha256_skipped"] = skipped
            result["sha256_mismatched"] = mismatched
            result["sha256_missing"] = missing
            result["verified"] = len(mismatched) == 0 and len(missing) == 0

        return result
    except Exception as e:
        return {
            "source_files": expected_file_count,
            "source_size": _format_size(expected_bytes),
            "verify_error": str(e),
        }


def _verify_hf_upload(
    hf_api,
    hf_repo_id: str,
    repo_type: str,
    expected_file_count: int,
    expected_bytes: int,
    source_sha256: dict[str, str] | None = None,
) -> dict:
    """Verify uploaded files on HuggingFace match the source.

    Args:
        source_sha256: Optional mapping of {path: sha256_hex} from the source
                       platform. If provided, enables per-file hash verification.
    """
    PLATFORM_FILES = {".gitattributes", "README.md"}

    try:
        if repo_type == "space":
            # space_info() does not support files_metadata — use without LFS hashes
            info = hf_api.space_info(hf_repo_id)
        elif repo_type == "dataset":
            info = hf_api.dataset_info(hf_repo_id, files_metadata=True)
        else:
            info = hf_api.model_info(hf_repo_id, files_metadata=True)

        siblings = getattr(info, "siblings", None)
        result = {
            "source_files": expected_file_count,
            "source_size": _format_size(expected_bytes),
        }
        if siblings is None:
            result["verify_error"] = "Destination file listing unavailable (API returned no file data)"
            return result
        if siblings is not None:
            dest_files = len(siblings)
            dest_size = sum(getattr(s, "size", 0) or 0 for s in siblings)
            result["dest_files"] = dest_files
            result["files_match"] = dest_files >= expected_file_count
            if dest_size > 0:
                result["dest_size"] = _format_size(dest_size)

            # SHA256 verification if source hashes provided
            if source_sha256:
                dest_sha_map = {}
                dest_all_paths = set()
                for s in siblings:
                    dest_all_paths.add(s.rfilename)
                    sha = None
                    if s.lfs and isinstance(s.lfs, dict):
                        sha = s.lfs.get("sha256")
                    elif s.lfs and hasattr(s.lfs, "sha256"):
                        sha = s.lfs.sha256
                    if sha:
                        dest_sha_map[s.rfilename] = sha

                matched = 0
                skipped = 0
                mismatched = []
                missing = []
                for path, src_sha in source_sha256.items():
                    if path in PLATFORM_FILES:
                        continue
                    if path not in dest_all_paths:
                        missing.append(path)
                        continue
                    dst_sha = dest_sha_map.get(path, "")
                    if src_sha and dst_sha and src_sha == dst_sha:
                        matched += 1
                    elif src_sha and dst_sha and src_sha != dst_sha:
                        mismatched.append(path)
                    else:
                        skipped += 1  # file exists but one or both hashes missing

                result["sha256_matched"] = matched
                result["sha256_skipped"] = skipped
                result["sha256_mismatched"] = mismatched
                result["sha256_missing"] = missing
                result["verified"] = len(mismatched) == 0 and len(missing) == 0

        return result
    except Exception as e:
        return {
            "source_files": expected_file_count,
            "source_size": _format_size(expected_bytes),
            "verify_error": str(e),
        }


def _print_verification(verify: dict) -> None:
    """Print a human-readable verification summary."""
    print()
    print("  Verification:")
    print(f"    Source files: {verify['source_files']}")
    print(f"    Source size:  {verify['source_size']}")
    if "dest_files" in verify:
        match = "OK" if verify.get("files_match") else "MISMATCH"
        print(f"    Dest files:  {verify['dest_files']}  [{match}]")
    if "dest_size" in verify:
        print(f"    Dest size:   {verify['dest_size']}")
    if "sha256_matched" in verify:
        print(f"    SHA256:      {verify['sha256_matched']} matched", end="")
        if verify.get("sha256_skipped"):
            print(f", {verify['sha256_skipped']} skipped (no hash)", end="")
        if verify.get("sha256_mismatched"):
            print(f", {len(verify['sha256_mismatched'])} MISMATCHED")
            for p in verify["sha256_mismatched"][:5]:
                print(f"                   - {p}")
        else:
            print()
        if verify.get("sha256_missing"):
            print(f"    Missing:     {len(verify['sha256_missing'])} files")
            for p in verify["sha256_missing"][:5]:
                print(f"                   - {p}")
        if verify.get("verified"):
            print("    Status:      VERIFIED")
        elif "verified" in verify:
            print("    Status:      FAILED")
    if "verify_error" in verify:
        print(f"    WARNING: Verification failed: {verify['verify_error']}")
        print("    Data integrity could not be confirmed.")
    elif "sha256_matched" not in verify and "dest_files" in verify:
        print("    SHA256:      not checked (source hashes unavailable)")



def _estimate_duration(size_bytes: int) -> str:
    """Estimate migration duration based on benchmark data.

    Benchmarks (cagliostrolab, 2026-02):
        1.2 GB  -> 1m 16s  (~16 MB/s overall)
        8.5 GB  -> 10m 20s (~14 MB/s overall)
        9.3 GB  -> 12m 55s (~12 MB/s overall)
        12.7 GB -> 8m 29s  (~25 MB/s overall, API download)
        95.5 GB -> ~2h      (~13 MB/s overall, git clone)

    Overall throughput averages ~14 MB/s across download + upload phases.
    Variation is 0.65x-1.5x depending on network conditions and repo structure.
    """
    gb = size_bytes / (1024 ** 3)
    if gb <= 0:
        return "< 1 minute"

    # ~14 MB/s overall throughput (download + upload combined)
    seconds = size_bytes / (14 * 1024 * 1024)

    low = seconds * 0.65
    high = seconds * 1.5
    return f"{_format_duration(low)} - {_format_duration(high)}"


def _ensure_ms_repo(
    api,
    ms_repo_id: str,
    repo_type: str,
    ms_token: str,
    private: bool = True,
) -> None:
    """Create a ModelScope repo if it doesn't exist. Fails fast on invalid namespace."""
    ms_visibility = 1 if private else 5
    vis_label = "private" if private else "public"
    if not api.repo_exists(repo_id=ms_repo_id, repo_type=repo_type, token=ms_token):
        if repo_type == "dataset":
            if "/" not in ms_repo_id:
                raise ValueError(f"Invalid ModelScope repo ID: '{ms_repo_id}'. Expected format: 'namespace/name'")
            namespace, name = ms_repo_id.split("/", 1)
            api.create_dataset(
                dataset_name=name,
                namespace=namespace,
                visibility=ms_visibility,
            )
        else:
            api.create_model(
                model_id=ms_repo_id,
                visibility=ms_visibility,
            )
        print(f"       Created new repo ({vis_label})")
    else:
        print("       Repo already exists, will update")


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
        return api.repo_exists(repo_id=repo_id, repo_type=repo_type, token=token)

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
        from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError

        api = HfApi(token=token)
        last_error = None

        for type_name, info_fn in [("model", api.model_info), ("dataset", api.dataset_info), ("space", api.space_info)]:
            try:
                info_fn(repo_id)
                return type_name
            except RepositoryNotFoundError:
                continue
            except GatedRepoError:
                return type_name  # repo exists but requires access agreement
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


@app.function(image=migrate_image, timeout=600)
def _list_hf_files(
    hf_repo_id: str,
    repo_type: str,
    hf_token: str,
) -> list[dict]:
    """Clone repo structure (no LFS content) and return file manifest.

    Returns list of dicts:
        [{"path": "weights/model.safetensors", "size": 4800000000, "is_lfs": True,
          "sha256": "abc123..."}, ...]
    The "sha256" key is only present for LFS files with valid pointer files.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    work_dir = tempfile.mkdtemp(prefix="hf_list_")
    try:
        # Build clone URL
        if repo_type == "dataset":
            clone_url = f"https://user:{hf_token}@huggingface.co/datasets/{hf_repo_id}"
        elif repo_type == "space":
            clone_url = f"https://user:{hf_token}@huggingface.co/spaces/{hf_repo_id}"
        else:
            clone_url = f"https://user:{hf_token}@huggingface.co/{hf_repo_id}"

        clone_dir = os.path.join(work_dir, "repo")

        # Clone structure only (no LFS content)
        env = os.environ.copy()
        env["GIT_LFS_SKIP_SMUDGE"] = "1"
        proc = subprocess.run(
            ["git", "clone", "--depth=1", clone_url, clone_dir],
            env=env, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            err = proc.stderr.replace(hf_token, "***")
            raise RuntimeError(f"git clone failed: {err}")

        # Get set of LFS-tracked files
        lfs_proc = subprocess.run(
            ["git", "lfs", "ls-files", "--long"],
            cwd=clone_dir, capture_output=True, text=True,
        )
        lfs_paths = set()
        if lfs_proc.returncode == 0:
            for line in lfs_proc.stdout.strip().splitlines():
                # Format: "<oid> <-> <path>" or "<oid> * <path>"
                parts = line.split(" ", 2)
                if len(parts) >= 3:
                    path = parts[2].strip()
                    if path:
                        lfs_paths.add(path)
        else:
            print(f"  WARNING: git lfs ls-files failed (rc={lfs_proc.returncode}), "
                  "treating all files as non-LFS. SHA256 from pointer files unavailable.")

        # Build manifest
        manifest = []
        for root, _dirs, files in os.walk(clone_dir):
            # Skip .git directory
            if ".git" in root.split(os.sep):
                continue
            for fname in files:
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, clone_dir)
                if relpath.startswith(".git"):
                    continue

                is_lfs = relpath in lfs_paths
                sha256 = None
                if is_lfs:
                    size, sha256 = _parse_lfs_pointer_full(fpath)
                    if size is None:
                        print(f"  WARNING: Could not parse LFS pointer for {relpath}, size unknown")
                        size = 0  # couldn't parse, will still be downloaded
                else:
                    try:
                        size = os.path.getsize(fpath)
                    except OSError:
                        size = 0

                entry = {
                    "path": relpath,
                    "size": size,
                    "is_lfs": is_lfs,
                }
                if sha256:
                    entry["sha256"] = sha256
                manifest.append(entry)

        print(f"  File manifest: {len(manifest)} files, "
              f"{len(lfs_paths)} LFS, "
              f"{len(manifest) - len(lfs_paths)} non-LFS, "
              f"total {_format_size(sum(f['size'] for f in manifest))}")
        return manifest
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.function(image=migrate_image, timeout=86400, max_containers=100)
def _migrate_chunk(
    hf_repo_id: str,
    ms_repo_id: str,
    repo_type: str,
    hf_token: str,
    ms_token: str,
    ms_domain: str,
    chunk_files: list[dict],
    chunk_index: int,
    total_chunks: int,
) -> dict:
    """Download and upload one chunk of files for parallel migration.

    Each chunk worker is self-contained: clones repo structure, pulls only
    assigned LFS files, prunes unassigned files, uploads to ModelScope.
    """
    import os
    import shutil
    import subprocess
    import tempfile
    import time as _time

    if ms_domain:
        os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)
    from modelscope.hub.api import HubApi

    start = _time.time()
    work_dir = tempfile.mkdtemp(prefix=f"chunk{chunk_index}_")
    assigned_paths = {f["path"] for f in chunk_files}
    lfs_paths = [f["path"] for f in chunk_files if f["is_lfs"]]

    try:
        # 1. Git clone (structure only, no LFS content)
        if repo_type == "dataset":
            clone_url = f"https://user:{hf_token}@huggingface.co/datasets/{hf_repo_id}"
        elif repo_type == "space":
            clone_url = f"https://user:{hf_token}@huggingface.co/spaces/{hf_repo_id}"
        else:
            clone_url = f"https://user:{hf_token}@huggingface.co/{hf_repo_id}"

        clone_dir = os.path.join(work_dir, "repo")
        env = os.environ.copy()
        env["GIT_LFS_SKIP_SMUDGE"] = "1"

        proc = subprocess.run(
            ["git", "clone", "--depth=1", clone_url, clone_dir],
            env=env, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            err = proc.stderr.replace(hf_token, "***")
            raise RuntimeError(f"git clone failed: {err}")

        print(f"  [Chunk {chunk_index}/{total_chunks}] Cloned structure")

        # 2. Selective LFS pull (only this chunk's files)
        if lfs_paths:
            # Use git config to avoid CLI argument length limits
            include_val = ",".join(lfs_paths)
            subprocess.run(
                ["git", "config", "lfs.fetchinclude", include_val],
                cwd=clone_dir, check=True, capture_output=True,
            )
            print(f"  [Chunk {chunk_index}/{total_chunks}] Pulling {len(lfs_paths)} LFS files...")

            lfs_proc = subprocess.Popen(
                ["git", "lfs", "pull"],
                cwd=clone_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            lfs_errors = []
            for line in lfs_proc.stdout:
                clean = line.rstrip().replace(hf_token, "***")
                if clean and any(k in clean.lower() for k in ("error", "fatal", "fail")):
                    lfs_errors.append(clean)
            lfs_proc.wait()
            if lfs_proc.returncode != 0:
                raise RuntimeError(
                    f"git lfs pull failed (exit {lfs_proc.returncode})"
                    + (f": {' '.join(lfs_errors[-3:])}" if lfs_errors else ""))
            if lfs_errors:
                print(f"  [Chunk {chunk_index}/{total_chunks}] LFS warnings: {'; '.join(lfs_errors[:3])}")

        dl_time = _time.time() - start
        print(f"  [Chunk {chunk_index}/{total_chunks}] Downloaded in {_format_duration(dl_time)}")

        # 3. Remove .git directory
        git_dir = os.path.join(clone_dir, ".git")
        if os.path.isdir(git_dir):
            shutil.rmtree(git_dir)

        # 4. Prune unassigned files
        for root, dirs, files in os.walk(clone_dir, topdown=False):
            for fname in files:
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, clone_dir)
                if relpath not in assigned_paths:
                    os.remove(fpath)
            # Remove empty directories
            for dname in dirs:
                dpath = os.path.join(root, dname)
                try:
                    os.rmdir(dpath)
                except OSError:
                    pass

        # Count actual files to upload
        file_count, total_bytes = _dir_stats(clone_dir)
        print(f"  [Chunk {chunk_index}/{total_chunks}] Uploading {file_count} files "
              f"({_format_size(total_bytes)}) to ModelScope...")

        # 5. Upload with retry
        api = HubApi()
        api.login(ms_token)

        upload_kwargs = {
            "repo_id": ms_repo_id,
            "folder_path": clone_dir,
            "token": ms_token,
        }
        if repo_type == "dataset":
            upload_kwargs["repo_type"] = "dataset"

        last_error = None
        for attempt in range(3):
            try:
                api.upload_folder(**upload_kwargs)
                break
            except (ConnectionError, TimeoutError, OSError) as e:
                last_error = e
                if attempt < 2:
                    wait = 5 * (3 ** attempt)  # 5s, 15s
                    print(f"  [Chunk {chunk_index}/{total_chunks}] Upload failed (attempt {attempt + 1}), "
                          f"retrying in {wait}s...")
                    _time.sleep(wait)
            except Exception as e:
                # Non-transient errors (auth, permission, bad request) — fail immediately
                raise RuntimeError(
                    f"Chunk {chunk_index} upload failed (non-retryable): {e}"
                ) from e
        else:
            raise RuntimeError(
                f"Chunk {chunk_index} upload failed after 3 attempts: {last_error}"
            )

        total_time = _time.time() - start
        ul_time = total_time - dl_time
        print(f"  [Chunk {chunk_index}/{total_chunks}] Done in {_format_duration(total_time)} "
              f"(dl: {_format_duration(dl_time)}, ul: {_format_duration(ul_time)})")

        return {
            "status": "success",
            "chunk_index": chunk_index,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "dl_time": dl_time,
            "ul_time": ul_time,
            "duration": _format_duration(total_time),
        }
    except Exception as e:
        import traceback as _tb
        err_msg = str(e).replace(hf_token, "***").replace(ms_token, "***")
        tb_str = _tb.format_exc().replace(hf_token, "***").replace(ms_token, "***")
        print(f"  [Chunk {chunk_index}/{total_chunks}] FAILED: {err_msg}")
        print(f"  [Chunk {chunk_index}/{total_chunks}] Traceback:\n{tb_str}")
        return {
            "status": "error",
            "chunk_index": chunk_index,
            "error": err_msg,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.function(image=migrate_image, timeout=120)
def _ensure_ms_repo_remote(
    ms_repo_id: str,
    repo_type: str,
    ms_token: str,
    ms_domain: str = "",
    private: bool = True,
) -> None:
    """Remote wrapper for _ensure_ms_repo, callable from local entrypoint."""
    import os
    if ms_domain:
        os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)
    from modelscope.hub.api import HubApi

    api = HubApi()
    api.login(ms_token)
    _ensure_ms_repo(api, ms_repo_id, repo_type, ms_token, private)


@app.function(image=migrate_image, timeout=600)
def _verify_parallel_upload(
    ms_repo_id: str,
    repo_type: str,
    ms_token: str,
    ms_domain: str,
    file_manifest: list[dict],
) -> dict:
    """Verify all files from the manifest exist on ModelScope after chunked upload."""
    import os
    if ms_domain:
        os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)
    from modelscope.hub.api import HubApi

    api = HubApi()
    api.login(ms_token)

    expected_files = len(file_manifest)
    expected_bytes = sum(f["size"] for f in file_manifest)

    # Build SHA256 map from the manifest (LFS files have sha256 from pointers)
    source_sha256 = {}
    for f in file_manifest:
        if f.get("sha256"):
            source_sha256[f["path"]] = f["sha256"]

    verify = _verify_ms_upload(
        api, ms_repo_id, repo_type, expected_files, expected_bytes,
        source_sha256=source_sha256 if source_sha256 else None,
    )
    _print_verification(verify)
    return verify


@app.function(image=migrate_image, timeout=86400)
def migrate_hf_to_ms(
    hf_repo_id: str,
    ms_repo_id: str,
    repo_type: str,
    hf_token: str,
    ms_token: str,
    ms_domain: str = "",
    private: bool = True,
) -> dict:
    """Download repo from HuggingFace and upload to ModelScope.

    Tries the HF Hub API first. If blocked (403 Forbidden due to org storage
    limit), automatically falls back to git clone + git lfs pull.

    Returns:
        Dict with status, url, file_count, total_size, and duration.
    """
    import os
    import shutil
    import tempfile
    import time as _time

    if ms_domain:
        os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)

    from modelscope.hub.api import HubApi

    start = _time.time()
    work_dir = tempfile.mkdtemp(prefix="hf_ms_migrate_")

    try:
        # Step 1: Create repo on ModelScope first (fail fast if namespace is invalid)
        print(f"[1/3] Ensuring ModelScope repo exists: {ms_repo_id}...")
        api = HubApi()
        api.login(ms_token)
        _ensure_ms_repo(api, ms_repo_id, repo_type, ms_token, private)

        # Step 2: Download from HuggingFace (API first, git fallback on 403)
        download_dir = os.path.join(work_dir, "download")
        used_git = False

        print(f"[2/3] Downloading {hf_repo_id} ({repo_type}) from HuggingFace...")
        try:
            from huggingface_hub import snapshot_download

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
        except Exception as dl_err:
            # Build a full error string including chained exceptions,
            # because HF wraps 403 inside LocalEntryNotFoundError with
            # a generic message that doesn't mention the actual HTTP status.
            error_parts = [str(dl_err)]
            cause = dl_err
            while cause := getattr(cause, '__cause__', None) or getattr(cause, '__context__', None):
                error_parts.append(str(cause))
            full_error = " ".join(error_parts)
            error_type = type(dl_err).__name__
            # HF returns 403 Forbidden when storage-locked. Git clone
            # bypasses this because git-based access is always available.
            # Do NOT fall back on 404/RepositoryNotFoundError — those mean
            # the repo genuinely doesn't exist and git clone would also fail.
            is_access_blocked = (
                "403" in full_error or "Forbidden" in full_error
                or error_type == "LocalEntryNotFoundError"
            )
            if is_access_blocked:
                print(f"       API blocked ({error_type}), falling back to git clone...")
                # Clean up failed download attempt
                if os.path.exists(download_dir):
                    shutil.rmtree(download_dir)
                local_dir, file_count, total_bytes = _git_clone_hf(
                    hf_repo_id, repo_type, hf_token, work_dir,
                )
                used_git = True
            else:
                raise

        # Step 3: Upload via HTTP API
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
        if used_git:
            print("       (Used git clone fallback due to API 403)")

        # Verify upload (with SHA256 from HF source)
        source_sha256 = _get_hf_sha256(hf_repo_id, repo_type, hf_token)
        verify = _verify_ms_upload(
            api, ms_repo_id, repo_type, file_count, total_bytes,
            source_sha256=source_sha256,
        )
        _print_verification(verify)

        return {
            "status": "success",
            "url": url,
            "file_count": file_count,
            "total_size": _format_size(total_bytes),
            "duration": _format_duration(total_time),
            "used_git_fallback": used_git,
            "verification": verify,
        }

    except Exception as e:
        import traceback
        total_time = _time.time() - start
        tb = traceback.format_exc()
        # Redact tokens from tracebacks
        tb = tb.replace(hf_token, "***").replace(ms_token, "***")
        error_str = str(e).replace(hf_token, "***").replace(ms_token, "***")
        print(f"ERROR after {_format_duration(total_time)}: {error_str}")
        print(f"Traceback:\n{tb}")
        return {
            "status": "error",
            "error": error_str,
            "traceback": tb,
            "duration": _format_duration(total_time),
        }

    finally:
        try:
            shutil.rmtree(work_dir)
        except OSError as e:
            print(f"WARNING: Failed to clean up {work_dir}: {e}")


@app.function(image=migrate_image, timeout=86400)
def migrate_hf_to_ms_git(
    hf_repo_id: str,
    ms_repo_id: str,
    repo_type: str,
    hf_token: str,
    ms_token: str,
    ms_domain: str = "",
    private: bool = True,
) -> dict:
    """Download repo from HuggingFace via git clone and upload to ModelScope.

    Uses git clone + git lfs pull instead of the HF Hub API. This bypasses
    the 403 Forbidden error when an org has exceeded its private storage limit
    (HF locks API downloads but git-based access still works).

    Prefer migrate_hf_to_ms() which tries the API first and falls back to git
    automatically. Use this function directly only when you want to force git mode.

    Returns:
        Dict with status, url, file_count, total_size, and duration.
    """
    import os
    import shutil
    import tempfile
    import time as _time

    if ms_domain:
        os.environ["MODELSCOPE_DOMAIN"] = _strip_protocol(ms_domain)

    from modelscope.hub.api import HubApi

    start = _time.time()
    work_dir = tempfile.mkdtemp(prefix="hf_ms_migrate_git_")

    try:
        # Step 1: Create repo on ModelScope first (fail fast if namespace is invalid)
        print(f"[1/3] Ensuring ModelScope repo exists: {ms_repo_id}...")
        api = HubApi()
        api.login(ms_token)
        _ensure_ms_repo(api, ms_repo_id, repo_type, ms_token, private)

        # Step 2: Git clone + LFS pull from HuggingFace
        print(f"[2/3] Cloning {hf_repo_id} ({repo_type}) from HuggingFace via git...")
        clone_dir, file_count, total_bytes = _git_clone_hf(
            hf_repo_id, repo_type, hf_token, work_dir,
        )

        # Step 3: Upload via HTTP API
        print(f"[3/3] Uploading {file_count} files ({_format_size(total_bytes)}) to ModelScope as {ms_repo_id}...")
        ul_start = _time.time()
        upload_kwargs = {
            "repo_id": ms_repo_id,
            "folder_path": clone_dir,
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

        # Verify upload (with SHA256 from HF source)
        source_sha256 = _get_hf_sha256(hf_repo_id, repo_type, hf_token)
        verify = _verify_ms_upload(
            api, ms_repo_id, repo_type, file_count, total_bytes,
            source_sha256=source_sha256,
        )
        _print_verification(verify)

        return {
            "status": "success",
            "url": url,
            "file_count": file_count,
            "total_size": _format_size(total_bytes),
            "duration": _format_duration(total_time),
            "verification": verify,
        }

    except Exception as e:
        import traceback
        total_time = _time.time() - start
        tb = traceback.format_exc()
        # Redact tokens from tracebacks
        tb = tb.replace(hf_token, "***").replace(ms_token, "***")
        error_str = str(e).replace(hf_token, "***").replace(ms_token, "***")
        print(f"ERROR after {_format_duration(total_time)}: {error_str}")
        print(f"Traceback:\n{tb}")
        return {
            "status": "error",
            "error": error_str,
            "traceback": tb,
            "duration": _format_duration(total_time),
        }

    finally:
        try:
            shutil.rmtree(work_dir)
        except OSError as e:
            print(f"WARNING: Failed to clean up {work_dir}: {e}")


@app.function(image=migrate_image, timeout=86400)
def migrate_ms_to_hf(
    ms_repo_id: str,
    hf_repo_id: str,
    repo_type: str,
    hf_token: str,
    ms_token: str,
    ms_domain: str = "",
    private: bool = True,
) -> dict:
    """Download repo from ModelScope and upload to HuggingFace.

    Creates the HuggingFace repo first (fail fast on invalid namespace),
    then downloads from ModelScope. Sanitizes README.md YAML front-matter
    (e.g., invalid license values) for HuggingFace compatibility before uploading.

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
        # Step 1: Create HuggingFace repo first (fail fast if namespace is invalid)
        print(f"[1/3] Ensuring HuggingFace repo exists: {hf_repo_id}...")
        hf_api = HfApi(token=hf_token)
        vis_label = "private" if private else "public"
        hf_api.create_repo(
            repo_id=hf_repo_id, repo_type=repo_type,
            private=private, exist_ok=True,
        )
        print(f"       Repo ready ({vis_label})")

        # Step 2: Download from ModelScope
        print(f"[2/3] Downloading {ms_repo_id} ({repo_type}) from ModelScope...")
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

        # Step 3: Upload to HuggingFace
        print(f"[3/3] Uploading {file_count} files ({_format_size(total_bytes)}) to HuggingFace as {hf_repo_id}...")
        ul_start = _time.time()
        hf_api.upload_folder(
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

        # Verify upload (with SHA256 from MS source)
        from modelscope.hub.api import HubApi as _MsApi
        ms_api = _MsApi()
        ms_api.login(ms_token)
        source_sha256 = _get_ms_sha256(ms_api, ms_repo_id, repo_type)
        verify = _verify_hf_upload(
            hf_api, hf_repo_id, repo_type, file_count, total_bytes,
            source_sha256=source_sha256,
        )
        _print_verification(verify)

        return {
            "status": "success",
            "url": url,
            "file_count": file_count,
            "total_size": _format_size(total_bytes),
            "duration": _format_duration(total_time),
            "verification": verify,
        }

    except Exception as e:
        import traceback
        total_time = _time.time() - start
        tb = traceback.format_exc()
        # Redact tokens from tracebacks
        tb = tb.replace(hf_token, "***").replace(ms_token, "***")
        error_str = str(e).replace(hf_token, "***").replace(ms_token, "***")
        print(f"ERROR after {_format_duration(total_time)}: {error_str}")
        print(f"Traceback:\n{tb}")
        return {
            "status": "error",
            "error": error_str,
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
    use_git: bool = False,
    parallel: bool = False,
    chunk_size: int = 20,
):
    """Migrate a repo between HuggingFace and ModelScope via Modal.

    Args:
        source: Source repo ID. Supports platform prefix: "hf:user/repo" or "ms:user/repo"
        to: Destination platform — "hf" or "ms". Required if source has no prefix.
        repo_type: "model", "dataset", or "space". Auto-detected if omitted.
        dest: Custom destination repo ID. Defaults to same as source.
        use_git: Use git clone + LFS instead of HF Hub API for download.
            Bypasses 403 storage-limit lockout on private repos.
        parallel: Use parallel chunked migration (fan out to multiple containers).
            Splits repo into chunks, each processed by an independent container.
            Auto-adjusts chunk size to cap at 100 concurrent containers.
            Currently supported for HF→MS direction only; ignored for MS→HF.
        chunk_size: Chunk size in GB for parallel mode (default: 20).
            Auto-increased for large repos to stay within the 100-container limit.
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

        # 5. Reject spaces when destination is ModelScope
        if repo_type == "space" and dst_plat == "ms":
            print()
            print("=" * 50)
            print("  SKIPPED: ModelScope does not support Spaces (Studios).")
            print("  ModelScope Studios can only be created via the web UI or git — the SDK has no support.")
            print("  To migrate space files as a model repo, use --repo-type model.")
            print("=" * 50)
            return

        # 6. Determine destination repo ID
        dest_repo_id = dest if dest else repo_id

        # 7. Summary
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

        # 8. Detect source repo visibility and size
        is_private = True  # default to private
        source_size_bytes = 0
        if src_plat == "hf":
            try:
                from huggingface_hub import HfApi
                hf_api = HfApi(token=hf_token)
                if repo_type == "dataset":
                    info = hf_api.dataset_info(repo_id, files_metadata=True)
                elif repo_type == "space":
                    info = hf_api.space_info(repo_id)
                else:
                    info = hf_api.model_info(repo_id, files_metadata=True)
                is_private = getattr(info, "private", True)
                vis_label = "private" if is_private else "public"
                print(f"  Source visibility: {vis_label}")
                # Extract size from file metadata
                siblings = getattr(info, "siblings", None)
                if siblings:
                    source_size_bytes = sum(getattr(s, "size", 0) or 0 for s in siblings)
                    if source_size_bytes > 0:
                        print(f"  Source size:       {_format_size(source_size_bytes)}")
                        print(f"  Estimated time:    {_estimate_duration(source_size_bytes)}")
            except Exception as e:
                err_str = str(e).lower()
                if any(k in err_str for k in ("401", "403", "unauthorized", "forbidden", "authentication")):
                    raise RuntimeError(f"Authentication failed querying source repo: {e}") from e
                print("  WARNING: Could not detect source visibility, defaulting to private")
        elif src_plat == "ms":
            try:
                import os as _os
                if ms_domain:
                    _os.environ["MODELSCOPE_DOMAIN"] = ms_domain
                from modelscope.hub.api import HubApi
                ms_api = HubApi()
                ms_api.login(ms_token)
                if repo_type == "dataset":
                    info = ms_api.get_dataset(repo_id)
                else:
                    info = ms_api.get_model(repo_id)
                # ModelScope: visibility=1 is private, visibility=5 is public
                ms_vis = info.get("visibility", 1) if isinstance(info, dict) else getattr(info, "visibility", 1)
                is_private = ms_vis != 5
                vis_label = "private" if is_private else "public"
                print(f"  Source visibility: {vis_label}")
                # Try to extract size from ModelScope info
                if isinstance(info, dict):
                    ds = info.get("DataSize") or info.get("data_size", 0)
                    if ds:
                        source_size_bytes = int(ds)
                        print(f"  Source size:       {_format_size(source_size_bytes)}")
                        print(f"  Estimated time:    {_estimate_duration(source_size_bytes)}")
            except Exception as e:
                err_str = str(e).lower()
                if any(k in err_str for k in ("401", "403", "unauthorized", "forbidden", "authentication")):
                    raise RuntimeError(f"Authentication failed querying source repo: {e}") from e
                print("  WARNING: Could not detect source visibility, defaulting to private")

        # 9. Check if destination repo already exists
        dest_token = ms_token if dst_plat == "ms" else hf_token
        dest_exists = check_repo_exists.remote(dest_repo_id, dst_plat, repo_type, dest_token, ms_domain)
        if dest_exists:
            print(f"  NOTE: {dest_repo_id} already exists on {dst_name}. Files will be updated/overwritten.")
            print()

        # 10. Run migration
        start = time.time()
        print("Starting migration...")
        print()

        if parallel and not (src_plat == "hf" and dst_plat == "ms"):
            print("  WARNING: --parallel is only supported for HF→MS direction. "
                  "Proceeding with single-container mode.")
            print()

        if src_plat == "hf" and dst_plat == "ms" and parallel:
            # --- Parallel chunked migration ---
            import time as _ptime

            MAX_PARALLEL = 100  # Hard ceiling (matches @app.function decorator)
            chunk_size_bytes = chunk_size * (1024 ** 3)

            # Phase 1: List files
            print(f"  (Parallel mode: {chunk_size} GB chunks)")
            print()
            print("[1/5] Listing files in source repo...")
            p_start = _ptime.time()
            file_manifest = _list_hf_files.remote(repo_id, repo_type, hf_token)
            total_files = len(file_manifest)
            total_size = sum(f["size"] for f in file_manifest)
            lfs_count = sum(1 for f in file_manifest if f["is_lfs"])
            print(f"       Found {total_files} files ({_format_size(total_size)}), "
                  f"{lfs_count} LFS")

            # Guardrail: auto-adjust chunk size to stay within MAX_PARALLEL containers
            if total_size > 0 and total_size / chunk_size_bytes > MAX_PARALLEL:
                chunk_size_bytes = total_size // MAX_PARALLEL + 1
                new_gb = chunk_size_bytes / (1024 ** 3)
                print(f"       Auto-adjusted chunk size: {chunk_size} GB -> "
                      f"{new_gb:.0f} GB (capped at {MAX_PARALLEL} containers)")

            # Guardrail: warn for repos with many small files (clone overhead per chunk)
            if total_files > 500_000:
                avg_file_mb = (total_size / total_files) / (1024 ** 2) if total_files else 0
                print(f"       Note: {total_files:,} files (avg {avg_file_mb:.1f} MB) — "
                      f"each chunk clones the full tree structure.")
                print(f"       Consider larger --chunk-size to reduce clone overhead.")

            # Phase 2: Build chunks
            print()
            print("[2/5] Planning chunks...")
            chunks = _build_chunks(file_manifest, chunk_size_bytes)
            print(f"       Split into {len(chunks)} chunks "
                  f"(max {MAX_PARALLEL} concurrent containers):")
            for i, chunk in enumerate(chunks):
                cs = sum(f["size"] for f in chunk)
                lfs_in_chunk = sum(1 for f in chunk if f["is_lfs"])
                non_lfs = len(chunk) - lfs_in_chunk
                label = f"Chunk {i}"
                if i == 0 and non_lfs > 0:
                    label += f" (metadata + {lfs_in_chunk} LFS)"
                else:
                    label += f" ({lfs_in_chunk} LFS files)"
                print(f"         {label}: {len(chunk)} files, {_format_size(cs)}")

            # Phase 3: Ensure MS repo exists
            print()
            print(f"[3/5] Ensuring ModelScope repo exists: {dest_repo_id}...")
            _ensure_ms_repo_remote.remote(
                dest_repo_id, repo_type, ms_token, ms_domain, is_private,
            )
            print("       OK")

            # Phase 4: Fan out chunk workers
            print()
            active = min(len(chunks), MAX_PARALLEL)
            print(f"[4/5] Migrating {len(chunks)} chunks "
                  f"({active} containers at a time)...")
            chunk_args = [
                (repo_id, dest_repo_id, repo_type, hf_token, ms_token, ms_domain,
                 chunks[i], i, len(chunks))
                for i in range(len(chunks))
            ]

            chunk_results = []
            for chunk_result in _migrate_chunk.starmap(chunk_args):
                idx = chunk_result.get("chunk_index", "?")
                status = chunk_result.get("status", "error")
                if status == "success":
                    print(f"  OK   Chunk {idx}: {chunk_result['file_count']} files, "
                          f"{_format_size(chunk_result['total_bytes'])}, "
                          f"{chunk_result['duration']}")
                else:
                    print(f"  FAIL Chunk {idx}: {chunk_result.get('error', 'Unknown')}")
                chunk_results.append(chunk_result)

            failed = [r for r in chunk_results if r.get("status") != "success"]
            succeeded = [r for r in chunk_results if r.get("status") == "success"]

            if failed:
                print(f"\n  {len(failed)} chunk(s) failed:")
                for r in failed:
                    print(f"    Chunk {r.get('chunk_index', '?')}: {r.get('error', 'Unknown')}")
                print("\n  Already-uploaded chunks are safe. Re-run with --parallel to retry.")

            # Phase 5: Verify
            verify = None
            if succeeded:
                print()
                print("[5/5] Verifying upload...")
                verify = _verify_parallel_upload.remote(
                    dest_repo_id, repo_type, ms_token, ms_domain, file_manifest,
                )

            p_total = _ptime.time() - p_start

            # Build result for reporting
            total_uploaded_files = sum(r.get("file_count", 0) for r in succeeded)
            total_uploaded_bytes = sum(r.get("total_bytes", 0) for r in succeeded)
            url = build_url(dest_repo_id, "ms", repo_type)

            result = {
                "status": "success" if not failed else ("error" if not succeeded else "partial"),
                "url": url,
                "file_count": total_uploaded_files,
                "total_size": _format_size(total_uploaded_bytes),
                "duration": _format_duration(p_total),
                "chunks_ok": len(succeeded),
                "chunks_failed": len(failed),
            }
            if succeeded and verify:
                result["verification"] = verify

        elif src_plat == "hf" and dst_plat == "ms":
            migrate_fn = migrate_hf_to_ms_git if use_git else migrate_hf_to_ms
            if use_git:
                print("  (Using git clone bypass for HF download)")
                print()
            result = migrate_fn.remote(
                hf_repo_id=repo_id,
                ms_repo_id=dest_repo_id,
                repo_type=repo_type,
                hf_token=hf_token,
                ms_token=ms_token,
                ms_domain=ms_domain,
                private=is_private,
            )
        elif src_plat == "ms" and dst_plat == "hf":
            result = migrate_ms_to_hf.remote(
                ms_repo_id=repo_id,
                hf_repo_id=dest_repo_id,
                repo_type=repo_type,
                hf_token=hf_token,
                ms_token=ms_token,
                ms_domain=ms_domain,
                private=is_private,
            )
        else:
            print(f"ERROR: Unsupported direction {src_plat} -> {dst_plat}")
            return

        # 11. Report
        print()
        print("=" * 50)
        if result["status"] in ("success", "partial"):
            if result["status"] == "partial":
                print("  Migration PARTIAL (some chunks failed)")
            else:
                print("  Migration complete!")
            print(f"  URL:      {result['url']}")
            print(f"  Files:    {result['file_count']}")
            print(f"  Size:     {result['total_size']}")
            print(f"  Duration: {result['duration']}")
            if "chunks_ok" in result:
                print(f"  Chunks:   {result['chunks_ok']} OK"
                      + (f", {result['chunks_failed']} failed" if result.get("chunks_failed") else ""))
        else:
            print("  Migration FAILED")
            if result.get("chunks_failed"):
                print(f"  All {result['chunks_failed']} chunk(s) failed")
            else:
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
                print("  - Repo may be too large. Try --parallel for large repos, or --repo-type to skip auto-detect")
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
        tb = traceback.format_exc()
        err_msg = str(e)
        # Redact tokens if they were loaded before the error
        for _tok in [locals().get("hf_token"), locals().get("ms_token")]:
            if _tok:
                tb = tb.replace(_tok, "***")
                err_msg = err_msg.replace(_tok, "***")
        print()
        print(f"Unexpected error: {err_msg}")
        print(tb)
        print("If this persists, check:")
        print("  - Modal account: modal token verify")
        print("  - Platform tokens: python scripts/validate_tokens.py")


@app.local_entrypoint()
def batch(
    source: str,
    to: str = "",
    repo_type: str = "model",
    use_git: bool = False,
):
    """Migrate multiple repos in parallel using multiple Modal containers.

    Args:
        source: Comma-separated repo IDs (e.g., "user/repo1,user/repo2,user/repo3").
            Can also use platform prefixes (e.g., "hf:user/repo1,hf:user/repo2").
        to: Destination platform — "hf" or "ms". Optional if repos use platform prefixes.
        repo_type: "model", "dataset", or "space". Applied to all repos (default: model).
        use_git: Use git clone + LFS instead of HF Hub API for download.
            Bypasses 403 storage-limit lockout on private repos.

    Note: --parallel is not supported in batch mode (use ::main per-repo instead).
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

        # Reject spaces when destination is ModelScope
        if repo_type == "space" and any(dst == "ms" for _, _, dst in jobs):
            print()
            print("=" * 60)
            print("  SKIPPED: ModelScope does not support Spaces (Studios).")
            print("  ModelScope Studios can only be created via the web UI or git — the SDK has no support.")
            print("  To migrate space files as model repos, use --repo-type model.")
            print("=" * 60)
            return

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
        checked_count = 0
        try:
            for i, exists in enumerate(check_repo_exists.starmap(check_args)):
                checked_count += 1
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
            unchecked = len(jobs) - checked_count
            print(f"  WARNING: Pre-check failed after {checked_count}/{len(jobs)} repos ({e}).")
            print(f"  {unchecked} unchecked repo(s) will NOT be skipped even if they already exist.")

        if existing:
            print(f"  Skipping {len(existing)} existing repo(s)")
        else:
            print("  No existing repos found, migrating all")
        print()

        # Detect visibility for source repos
        repo_privacy = {}
        active_repos = [(repo_id, src_plat) for repo_id, src_plat, _ in jobs if repo_id not in existing]
        if active_repos:
            print("Detecting source repo visibility...")
            # HF source repos
            hf_source_repos = [rid for rid, sp in active_repos if sp == "hf"]
            if hf_source_repos:
                try:
                    from huggingface_hub import HfApi
                    hf_api = HfApi(token=hf_token)
                    for rid in hf_source_repos:
                        try:
                            if repo_type == "dataset":
                                info = hf_api.dataset_info(rid)
                            elif repo_type == "space":
                                info = hf_api.space_info(rid)
                            else:
                                info = hf_api.model_info(rid)
                            repo_privacy[rid] = getattr(info, "private", True)
                        except Exception:
                            repo_privacy[rid] = True  # default private
                            print(f"    WARNING: Could not detect visibility for {rid}, defaulting to private")
                except Exception:
                    for rid in hf_source_repos:
                        repo_privacy[rid] = True
                    print(f"    WARNING: HF API init failed, defaulting all {len(hf_source_repos)} repos to private")
            # MS source repos
            ms_source_repos = [rid for rid, sp in active_repos if sp == "ms"]
            if ms_source_repos:
                try:
                    import os as _os
                    if ms_domain:
                        _os.environ["MODELSCOPE_DOMAIN"] = ms_domain
                    from modelscope.hub.api import HubApi
                    ms_api = HubApi()
                    ms_api.login(ms_token)
                    for rid in ms_source_repos:
                        try:
                            if repo_type == "dataset":
                                info = ms_api.get_dataset(rid)
                            else:
                                info = ms_api.get_model(rid)
                            ms_vis = info.get("visibility", 1) if isinstance(info, dict) else getattr(info, "visibility", 1)
                            repo_privacy[rid] = ms_vis != 5
                        except Exception:
                            repo_privacy[rid] = True
                            print(f"    WARNING: Could not detect visibility for {rid}, defaulting to private")
                except Exception:
                    for rid in ms_source_repos:
                        repo_privacy[rid] = True
                    print(f"    WARNING: MS API init failed, defaulting all {len(ms_source_repos)} repos to private")
            n_priv = sum(1 for v in repo_privacy.values() if v)
            n_pub = sum(1 for v in repo_privacy.values() if not v)
            print(f"  {n_priv} private, {n_pub} public")

        # Build starmap args based on direction (excluding existing)
        start = time.time()
        hf_to_ms_args = []
        ms_to_hf_args = []
        for repo_id, src_plat, dst_plat in jobs:
            if repo_id in existing:
                continue
            is_priv = repo_privacy.get(repo_id, True)
            if src_plat == "hf" and dst_plat == "ms":
                hf_to_ms_args.append((repo_id, repo_id, repo_type, hf_token, ms_token, ms_domain, is_priv))
            elif src_plat == "ms" and dst_plat == "hf":
                ms_to_hf_args.append((repo_id, repo_id, repo_type, hf_token, ms_token, ms_domain, is_priv))

        total_to_migrate = len(hf_to_ms_args) + len(ms_to_hf_args)
        if total_to_migrate == 0:
            print("All repos already exist on destination. Nothing to migrate.")
            return

        if use_git:
            print(f"Launching {total_to_migrate} parallel containers (git clone mode)...")
        else:
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
                err_msg = str(e)
                if hf_token:
                    err_msg = err_msg.replace(hf_token, "***")
                if ms_token:
                    err_msg = err_msg.replace(ms_token, "***")
                print(f"\n  BATCH ERROR ({label}): {err_msg}")
                print(f"  {len(results)} repos completed before failure.")
                if in_flight:
                    print(f"  Status unknown for: {', '.join(in_flight)}")

        hf_to_ms_fn = migrate_hf_to_ms_git if use_git else migrate_hf_to_ms
        _run_starmap(hf_to_ms_fn, hf_to_ms_args, "HF->MS", results)
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
        tb = traceback.format_exc()
        err_msg = str(e)
        # Redact tokens if they were loaded before the error
        for _tok in [locals().get("hf_token"), locals().get("ms_token")]:
            if _tok:
                tb = tb.replace(_tok, "***")
                err_msg = err_msg.replace(_tok, "***")
        print()
        print(f"Unexpected error: {err_msg}")
        print(tb)
        print("If this persists, check:")
        print("  - Modal account: modal token verify")
        print("  - Platform tokens: python scripts/validate_tokens.py")
