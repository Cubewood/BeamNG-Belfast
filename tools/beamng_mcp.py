"""Minimal client for BeamNG's built-in MCP server (Streamable HTTP, JSON-RPC 2.0).

Protocol facts (docs/spikes/mcp.md, observed live): single route POST /mcp, no session
header, no auth, plain application/json replies, notifications get 202 with an empty body.
Several tools are async: the first call triggers the work and returns text, a later call
returns the result (screenshot_image is the one this module polls for).

This module never starts the game and never talks to anything but the given URL.
"""
from __future__ import annotations

import base64
import json
import time

import requests

HOST = "127.0.0.1"
PORT = 29292
URL = f"http://{HOST}:{PORT}/mcp"
PROTOCOL_VERSION = "2025-11-25"
CLIENT_INFO = {"name": "belfast-gamectl", "version": "1"}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
HTTP_TIMEOUT = (3, 30)  # connect, read


class McpError(RuntimeError):
    """Transport or protocol failure (also raised for JSON-RPC error replies)."""


class ToolError(McpError):
    """A tool ran and reported isError=true."""


def build_request(method: str, params: dict | None = None, req_id: int | None = None) -> dict:
    """JSON-RPC request body. req_id=None builds a notification (no id)."""
    body: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if req_id is not None:
        body["id"] = req_id
    return body


def tool_call_params(name: str, arguments: dict | None = None) -> dict:
    return {"name": name, "arguments": arguments or {}}


def result_text(result: dict) -> str:
    """Concatenate the text items of a tools/call result."""
    return "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def result_image(result: dict) -> bytes | None:
    """Decoded bytes of the first image item, or None."""
    for c in result.get("content", []):
        if c.get("type") == "image" and c.get("data"):
            return base64.b64decode(c["data"])
    return None


class McpClient:
    def __init__(self, url: str = URL, session=None, timeout=HTTP_TIMEOUT):
        self.url = url
        self.timeout = timeout
        self._http = session if session is not None else requests.Session()
        self._next_id = 0

    # -- transport ---------------------------------------------------------
    def rpc(self, method: str, params: dict | None = None, notify: bool = False,
            timeout=None) -> dict | None:
        """Send one request. Returns the 'result' dict (None for notifications)."""
        req_id = None
        if not notify:
            self._next_id += 1
            req_id = self._next_id
        body = build_request(method, params, req_id)
        try:
            r = self._http.post(self.url, json=body, headers=HEADERS, timeout=timeout or self.timeout)
        except requests.RequestException as e:
            raise McpError(f"{method}: {e}") from e
        if notify:
            if r.status_code not in (200, 202, 204):
                raise McpError(f"{method}: HTTP {r.status_code}")
            return None
        if r.status_code != 200:
            raise McpError(f"{method}: HTTP {r.status_code} {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as e:
            raise McpError(f"{method}: reply is not JSON: {r.text[:200]!r}") from e
        if "error" in data:
            raise McpError(f"{method}: {data['error']}")
        return data.get("result", {})

    def initialize(self, timeout=None) -> dict:
        res = self.rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                      "clientInfo": CLIENT_INFO}, timeout=timeout)
        self.rpc("notifications/initialized", notify=True, timeout=timeout)
        return res

    def ping(self, timeout=None) -> None:
        self.rpc("ping", timeout=timeout)

    def list_tools(self) -> list[dict]:
        return self.rpc("tools/list")["tools"]

    # -- tools -------------------------------------------------------------
    def call(self, name: str, arguments: dict | None = None, timeout=None) -> dict:
        """tools/call; returns the raw result dict. Raises ToolError when isError is true."""
        res = self.rpc("tools/call", tool_call_params(name, arguments), timeout=timeout)
        if res.get("isError"):
            raise ToolError(f"{name}: {result_text(res)[:300]}")
        return res

    def call_text(self, name: str, arguments: dict | None = None, timeout=None) -> str:
        return result_text(self.call(name, arguments, timeout=timeout))

    def call_json(self, name: str, arguments: dict | None = None, timeout=None):
        text = self.call_text(name, arguments, timeout=timeout)
        try:
            return json.loads(text)
        except ValueError as e:
            raise McpError(f"{name}: reply is not JSON: {text[:200]!r}") from e

    def get_status(self, timeout=None) -> dict:
        return self.call_json("get_status", timeout=timeout)

    def run_lua(self, code: str, timeout=None) -> str:
        return self.call_text("run_lua", {"code": code}, timeout=timeout)

    def run_lua_json(self, code: str, timeout=None):
        """Run Lua whose last statement returns jsonEncode(...), decode the text."""
        text = self.run_lua(code, timeout=timeout)
        try:
            return json.loads(text)
        except ValueError as e:
            raise McpError(f"run_lua: reply is not JSON: {text[:200]!r}") from e

    def load_level(self, name: str) -> str:
        return self.call_text("load_level", {"name": name})

    def screenshot_image(self, scale: float = 1.0, timeout: float = 30.0, poll: float = 0.5,
                         sleep=time.sleep, clock=time.monotonic) -> bytes:
        """Trigger a capture and poll until the image arrives (two-call async behaviour)."""
        deadline = clock() + timeout
        last = ""
        while True:
            res = self.call("screenshot_image", {"scale": scale})
            img = result_image(res)
            if img:
                return img
            last = result_text(res)
            if clock() >= deadline:
                raise McpError(f"screenshot_image: no image within {timeout}s (last reply: {last[:120]!r})")
            sleep(poll)
