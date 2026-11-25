#!/usr/bin/env python3
"""Launcher for the 1Password host plugin supporting official 1password-mcp and op CLI modes."""

import json
import os
import shutil
import subprocess
import sys
import threading
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


def _run_official_proxy(binary: str) -> int:
    """Proxy stdio to 1password-mcp, handling pre-initialize probes (e.g. server/discover)."""
    env = os.environ.copy()
    env.setdefault("OP_MCP_SOURCE", "plugin")

    proc = subprocess.Popen(
        [binary],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        env=env,
        bufsize=0,
    )
    assert proc.stdin is not None and proc.stdout is not None

    def _pump_stdout() -> None:
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
        except BrokenPipeError:
            pass

    reader = threading.Thread(target=_pump_stdout, daemon=True)
    reader.start()

    initialized = False
    try:
        while True:
            raw = sys.stdin.buffer.readline()
            if not raw:
                break
            if not initialized:
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    msg = None
                if isinstance(msg, dict) and msg.get("method") != "initialize":
                    req_id = msg.get("id")
                    if req_id is not None:
                        err = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32601,
                                "message": f"Method not found: {msg.get('method')}",
                            },
                        }
                        sys.stdout.buffer.write(
                            (json.dumps(err) + "\n").encode("utf-8")
                        )
                        sys.stdout.buffer.flush()
                    continue
                initialized = True
            proc.stdin.write(raw)
            proc.stdin.flush()
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass

    rc = proc.wait()
    reader.join(timeout=2)
    return rc


def main() -> None:
    mode = os.environ.get("OP_PLUGIN_MODE", "official-mcp").strip() or "official-mcp"
    here = Path(__file__).resolve().parent

    if mode == "official-mcp":
        binary = _find_official_mcp()
        if binary:
            sys.exit(_run_official_proxy(binary))
        sys.stderr.write(
            "1password-mcp binary not found; falling back to op CLI server.py\n"
        )

    server_py = str(here / "server.py")
    os.execv(sys.executable, [sys.executable, server_py])


if __name__ == "__main__":
    main()
