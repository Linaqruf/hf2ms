"""Validate all required platform tokens for HF-Modal-ModelScope migration.

Usage:
    python scripts/validate_tokens.py
"""

from __future__ import annotations

import os
import sys


def check_env(name: str) -> str | None:
    """Return token value if set, None otherwise."""
    val = os.environ.get(name, "").strip()
    return val if val else None


def validate_hf_token(token: str) -> tuple[bool, str]:
    """Validate HuggingFace token by calling whoami."""
    try:
        from huggingface_hub import whoami

        info = whoami(token=token)
        return True, f"Authenticated as: {info.get('name', 'unknown')}"
    except ImportError:
        return False, "huggingface_hub not installed"
    except Exception as e:
        return False, f"Invalid token: {e}"


def validate_modelscope_token(token: str) -> tuple[bool, str]:
    """Validate ModelScope token by attempting login."""
    try:
        from modelscope.hub.api import HubApi

        api = HubApi()
        api.login(token)
        domain = os.environ.get("MODELSCOPE_DOMAIN", "modelscope.cn")
        return True, f"Login successful (domain: {domain})"
    except ImportError:
        return False, "modelscope not installed"
    except Exception as e:
        return False, f"Invalid token: {e}"


def validate_modal_tokens(token_id: str | None, token_secret: str | None) -> tuple[bool, str]:
    """Check Modal tokens are present (actual validation requires modal CLI)."""
    if token_id and token_secret:
        return True, "Tokens present (run `modal token verify` to fully validate)"
    missing = []
    if not token_id:
        missing.append("MODAL_TOKEN_ID")
    if not token_secret:
        missing.append("MODAL_TOKEN_SECRET")
    return False, f"Missing: {', '.join(missing)}"


def main() -> int:
    """Run all token validations and print results."""
    print("=" * 60)
    print("HF-Modal-ModelScope Token Validation")
    print("=" * 60)

    ms_domain = os.environ.get("MODELSCOPE_DOMAIN", "modelscope.cn").strip().rstrip("/")
    token_urls = {
        "HF_TOKEN": "https://huggingface.co/settings/tokens",
        "MODAL_TOKEN_ID": "modal token new",
        "MODAL_TOKEN_SECRET": "modal token new",
        "MODELSCOPE_TOKEN": f"https://{ms_domain}/my/myaccesstoken",
    }

    all_ok = True

    # Check HuggingFace
    hf_token = check_env("HF_TOKEN")
    if hf_token:
        ok, msg = validate_hf_token(hf_token)
        status = "OK" if ok else "FAIL"
        print(f"\n[{status}] HF_TOKEN: {msg}")
        if not ok:
            all_ok = False
    else:
        all_ok = False
        print(f"\n[MISSING] HF_TOKEN")
        print(f"  Get it from: {token_urls['HF_TOKEN']}")

    # Check Modal
    modal_id = check_env("MODAL_TOKEN_ID")
    modal_secret = check_env("MODAL_TOKEN_SECRET")
    ok, msg = validate_modal_tokens(modal_id, modal_secret)
    status = "OK" if ok else "FAIL"
    print(f"\n[{status}] Modal tokens: {msg}")
    if not ok:
        all_ok = False
        print(f"  Run: {token_urls['MODAL_TOKEN_ID']}")

    # Check ModelScope
    ms_token = check_env("MODELSCOPE_TOKEN")
    if ms_token:
        ok, msg = validate_modelscope_token(ms_token)
        status = "OK" if ok else "FAIL"
        print(f"\n[{status}] MODELSCOPE_TOKEN: {msg}")
        if not ok:
            all_ok = False
    else:
        all_ok = False
        print(f"\n[MISSING] MODELSCOPE_TOKEN")
        print(f"  Get it from: {token_urls['MODELSCOPE_TOKEN']}")

    print()
    print("=" * 60)
    if all_ok:
        print("All tokens valid. Ready to migrate!")
    else:
        print("Some tokens missing or invalid. Fix the issues above.")
    print("=" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
