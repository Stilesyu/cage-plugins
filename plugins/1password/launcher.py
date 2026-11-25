#!/usr/bin/env python3
"""Launcher for the 1Password host plugin supporting official 1password-mcp and op CLI modes."""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

OFFICIAL_CANDIDATES = (
    "/usr/local/bin/1password-mcp",
    "/opt/homebrew/bin/1password-mcp",
    "/Applications/1Password.app/Contents/MacOS/1password-mcp",
)


def _find_official_mcp() -> Optional[str]:
    found = shutil.which("1password-mcp") or shutil.which("onepassword-mcp")
    if found:
        return found
    for path in OFFICIAL_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def main() -> None:
    mode = os.environ.get("OP_PLUGIN_MODE", "official-mcp").strip() or "official-mcp"
    here = Path(__file__).resolve().parent

    if mode == "official-mcp":
        binary = _find_official_mcp()
        if binary:
            env = os.environ.copy()
            env.setdefault("OP_MCP_SOURCE", "plugin")
            os.execve(binary, [binary], env)
        # Fall back to op CLI server if official binary is not present
        sys.stderr.write(
            "1password-mcp binary not found; falling back to op CLI server.py\n"
        )

    server_py = str(here / "server.py")
    os.execv(sys.executable, [sys.executable, server_py])


if __name__ == "__main__":
    main()
