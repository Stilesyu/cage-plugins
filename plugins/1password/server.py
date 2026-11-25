#!/usr/bin/env python3
"""Minimal zero-dependency MCP stdio server for the 1Password CLI (op)."""

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "onepassword-mcp"
SERVER_VERSION = "0.1.0"
CMD_TIMEOUT = 30


def _find_op() -> Optional[str]:
    op_path = shutil.which("op")
    if op_path:
        return op_path
    for candidate in ("/opt/homebrew/bin/op", "/usr/local/bin/op", "/usr/bin/op"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run_op(args: List[str]) -> Dict[str, Any]:
    op_bin = _find_op()
    if not op_bin:
        return {
            "ok": False,
            "error": (
                "1Password CLI (op) was not found on PATH. "
                "Install it (e.g. `brew install --cask 1password-cli`) and enable "
                "CLI integration in 1Password Desktop -> Settings -> Developer."
            ),
        }

    cmd = [op_bin]
    account = os.environ.get("OP_ACCOUNT", "").strip()
    if account and "--account" not in args:
        cmd.extend(["--account", account])
    cmd.extend(args)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CMD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Command timed out after {CMD_TIMEOUT}s."}
    except OSError as exc:
        return {"ok": False, "error": f"Failed to execute op: {exc}"}

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"op exited with code {proc.returncode}"
        return {"ok": False, "error": err}

    return {"ok": True, "stdout": proc.stdout.strip()}


def _text_result(text: str, is_error: bool = False) -> Dict[str, Any]:
    res: Dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        res["isError"] = True
    return res


def _json_result(data: Any, is_error: bool = False) -> Dict[str, Any]:
    return _text_result(json.dumps(data, ensure_ascii=False, indent=2), is_error=is_error)


def _redact_item(item: Dict[str, Any]) -> Dict[str, Any]:
    safe = {
        "id": item.get("id"),
        "title": item.get("title"),
        "category": item.get("category"),
        "vault": item.get("vault"),
        "tags": item.get("tags", []),
        "urls": item.get("urls", []),
        "updated_at": item.get("updated_at"),
    }
    fields = []
    for f in item.get("fields", []) or []:
        ftype = f.get("type", "")
        purpose = f.get("purpose", "")
        is_secret = ftype == "CONCEALED" or purpose in ("PASSWORD", "NOTES")
        entry = {
            "id": f.get("id"),
            "label": f.get("label"),
            "type": ftype,
            "purpose": purpose,
            "reference": f.get("reference"),
            "value": "[REDACTED - use op_read_secret with reference]" if is_secret else f.get("value"),
        }
        fields.append(entry)
    safe["fields"] = fields
    return safe


TOOLS = [
    {
        "name": "op_status",
        "description": "Check whether the 1Password CLI (op) is installed and list signed-in accounts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "op_list_vaults",
        "description": "List accessible 1Password vaults (id and name).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "op_list_items",
        "description": "List 1Password item summaries (id, title, category, vault, urls) without revealing secrets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault": {"type": "string", "description": "Optional vault name or ID to filter."},
                "categories": {"type": "string", "description": "Optional comma-separated categories (e.g. Login,API Credential)."},
                "tags": {"type": "string", "description": "Optional comma-separated tags to filter."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "op_get_item",
        "description": "Get metadata, non-secret fields, and op:// secret references for a 1Password item. Secret values are redacted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Item title or ID."},
                "vault": {"type": "string", "description": "Optional vault name or ID."},
            },
            "required": ["item"],
            "additionalProperties": False,
        },
    },
    {
        "name": "op_read_secret",
        "description": "Read a specific secret value using an op:// reference (e.g. op://Vault/Item/field).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "1Password secret reference URI starting with op://",
                },
            },
            "required": ["reference"],
            "additionalProperties": False,
        },
    },
    {
        "name": "op_get_otp",
        "description": "Get the current one-time password (TOTP) code for a 1Password item.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Item title or ID."},
                "vault": {"type": "string", "description": "Optional vault name or ID."},
            },
            "required": ["item"],
            "additionalProperties": False,
        },
    },
]


def handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "op_status":
        ver = _run_op(["--version"])
        if not ver["ok"]:
            return _json_result({"installed": False, "error": ver["error"]}, is_error=True)
        accounts_res = _run_op(["account", "list", "--format", "json"])
        accounts = []
        if accounts_res["ok"] and accounts_res["stdout"]:
            try:
                accounts = json.loads(accounts_res["stdout"])
            except json.JSONDecodeError:
                accounts = []
        return _json_result({
            "installed": True,
            "version": ver["stdout"],
            "accounts": accounts,
            "signed_in": len(accounts) > 0,
        })

    if name == "op_list_vaults":
        res = _run_op(["vault", "list", "--format", "json"])
        if not res["ok"]:
            return _text_result(res["error"], is_error=True)
        return _text_result(res["stdout"] or "[]")

    if name == "op_list_items":
        cmd = ["item", "list", "--format", "json"]
        if args.get("vault"):
            cmd.extend(["--vault", str(args["vault"])])
        if args.get("categories"):
            cmd.extend(["--categories", str(args["categories"])])
        if args.get("tags"):
            cmd.extend(["--tags", str(args["tags"])])
        res = _run_op(cmd)
        if not res["ok"]:
            return _text_result(res["error"], is_error=True)
        return _text_result(res["stdout"] or "[]")

    if name == "op_get_item":
        item_name = str(args.get("item", "")).strip()
        if not item_name:
            return _text_result("Missing required parameter: item", is_error=True)
        cmd = ["item", "get", item_name, "--format", "json"]
        if args.get("vault"):
            cmd.extend(["--vault", str(args["vault"])])
        res = _run_op(cmd)
        if not res["ok"]:
            return _text_result(res["error"], is_error=True)
        try:
            parsed = json.loads(res["stdout"])
        except json.JSONDecodeError:
            return _text_result("Failed to parse item JSON output.", is_error=True)
        return _json_result(_redact_item(parsed))

    if name == "op_read_secret":
        ref = str(args.get("reference", "")).strip()
        if not ref.startswith("op://"):
            return _text_result("reference must start with op://", is_error=True)
        res = _run_op(["read", "--no-newline", ref])
        if not res["ok"]:
            return _text_result(res["error"], is_error=True)
        return _text_result(res["stdout"])

    if name == "op_get_otp":
        item_name = str(args.get("item", "")).strip()
        if not item_name:
            return _text_result("Missing required parameter: item", is_error=True)
        cmd = ["item", "get", item_name, "--otp"]
        if args.get("vault"):
            cmd.extend(["--vault", str(args["vault"])])
        res = _run_op(cmd)
        if not res["ok"]:
            return _text_result(res["error"], is_error=True)
        return _text_result(res["stdout"])

    return _text_result(f"Unknown tool: {name}", is_error=True)


def _send(msg: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        if method == "notifications/initialized" or req_id is None:
            continue

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            })
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            if not isinstance(tool_args, dict):
                _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Invalid arguments"}})
                continue
            result = handle_tool_call(tool_name, tool_args)
            _send({"jsonrpc": "2.0", "id": req_id, "result": result})
        else:
            _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})


if __name__ == "__main__":
    main()
