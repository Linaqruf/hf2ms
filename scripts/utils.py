"""Shared utilities for HF-Modal-ModelScope migration."""

from __future__ import annotations

import os
import re
from pathlib import Path


def load_dotenv() -> None:
    """Load .env file from plugin root into os.environ (without overwriting existing vars).

    Custom implementation — no python-dotenv dependency required.
    Searches CLAUDE_PLUGIN_ROOT first, then the repo root (parent of scripts/).
    """
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    candidates = [
        Path(plugin_root) / ".env" if plugin_root else None,
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for env_path in candidates:
        if env_path and env_path.is_file():
            try:
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, value = line.partition("=")
                        key = key.strip()
                        if key.startswith("export "):
                            key = key[7:].strip()
                        value = value.strip()
                        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                            value = value[1:-1]
                        if key and key not in os.environ:
                            os.environ[key] = value
            except (OSError, UnicodeDecodeError) as e:
                print(f"  WARNING: Could not read {env_path}: {e}")
            break


def get_ms_domain() -> str:
    """Get the ModelScope domain from environment, defaulting to modelscope.cn.

    Returns bare domain (e.g. 'modelscope.ai') — no protocol prefix.
    The ModelScope SDK expects this format for MODELSCOPE_DOMAIN env var.
    """
    domain = os.environ.get("MODELSCOPE_DOMAIN", "modelscope.cn").strip().rstrip("/")
    # Strip protocol if user included it
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    return domain


def get_env_token(name: str) -> str:
    """Get a token from environment variables.

    Raises ValueError with a helpful message if not found.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        ms_domain = get_ms_domain()
        token_urls = {
            "HF_TOKEN": "https://huggingface.co/settings/tokens",
            "MODAL_TOKEN_ID": "Run `modal token new` or visit https://modal.com/settings",
            "MODAL_TOKEN_SECRET": "Run `modal token new` or visit https://modal.com/settings",
            "MODELSCOPE_TOKEN": f"https://{ms_domain}/my/myaccesstoken",
        }
        hint = token_urls.get(name, "Check your platform settings")
        raise ValueError(f"{name} not set. Get it from: {hint}")
    return value


def parse_repo_id(user_input: str) -> tuple[str, str | None]:
    """Parse a repo identifier, optionally prefixed with platform hint.

    Accepted formats:
        "username/repo-name"           -> ("username/repo-name", None)
        "hf:username/repo-name"        -> ("username/repo-name", "hf")
        "ms:username/repo-name"        -> ("username/repo-name", "ms")
        "modelscope:username/repo"     -> ("username/repo", "ms")
        "huggingface:username/repo"    -> ("username/repo", "hf")

    Returns:
        (repo_id, platform) where platform is "hf", "ms", or None.
    """
    user_input = user_input.strip()

    platform_prefixes = {
        "hf:": "hf",
        "huggingface:": "hf",
        "ms:": "ms",
        "modelscope:": "ms",
    }

    platform = None
    for prefix, plat in platform_prefixes.items():
        if user_input.lower().startswith(prefix):
            user_input = user_input[len(prefix):]
            platform = plat
            break

    # Validate repo_id format: namespace/name
    if not re.match(r"^[\w.~-]+/[\w.~-]+$", user_input):
        raise ValueError(
            f"Invalid repo ID: '{user_input}'. Expected format: 'username/repo-name'"
        )

    return user_input, platform


def detect_direction(source_platform: str | None, to_flag: str | None) -> tuple[str, str]:
    """Determine migration direction from parsed inputs.

    Returns:
        (source_platform, dest_platform) — each is "hf" or "ms".
    """
    if to_flag:
        to_flag = to_flag.lower().strip()
        valid_ms = ("ms", "modelscope")
        valid_hf = ("hf", "huggingface")
        if to_flag in valid_ms:
            dest = "ms"
        elif to_flag in valid_hf:
            dest = "hf"
        else:
            raise ValueError(f"Invalid --to value: '{to_flag}'. Must be one of: hf, huggingface, ms, modelscope")
        # When source_platform is explicit, use it; otherwise infer as opposite
        if source_platform:
            if source_platform == dest:
                raise ValueError(f"Source platform '{source_platform}' and destination '{dest}' are the same")
            return source_platform, dest
        source = "hf" if dest == "ms" else "ms"
        return source, dest

    if source_platform:
        dest = "ms" if source_platform == "hf" else "hf"
        return source_platform, dest

    # Ambiguous — caller should ask the user
    raise ValueError("Cannot determine migration direction. Use --to hf|ms or prefix repo with hf:/ms:")


def build_url(repo_id: str, platform: str, repo_type: str = "model") -> str:
    """Build the web URL for a repo on the given platform.

    Args:
        repo_id: "namespace/name"
        platform: "hf" or "ms"
        repo_type: "model", "dataset", or "space"
    """
    if platform == "hf":
        type_prefix = {"model": "", "dataset": "datasets/", "space": "spaces/"}
        return f"https://huggingface.co/{type_prefix.get(repo_type, '')}{repo_id}"

    # ModelScope
    ms_domain = get_ms_domain()
    type_prefix = {"model": "models/", "dataset": "datasets/"}
    return f"https://{ms_domain}/{type_prefix.get(repo_type, 'models/')}{repo_id}"
