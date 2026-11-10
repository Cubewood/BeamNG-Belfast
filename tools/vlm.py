#!/usr/bin/env python3
"""Local vision-language model helper (LM Studio, Qwen3.8 27B).

CLI:  vlm.py start | load | status | unload
      vlm.py judge IMAGE --schema S.json --prompt "..." [--reasoning low|medium]
      vlm.py batch DIR --schema S.json --prompt "..." --out results.jsonl
Python: with vlm.session() as s: s.judge(path, schema, prompt)

GPU rule: the game and the model never overlap. Loading refuses while BeamNG
runs and takes the lock file H:\\BelfastData\\gpu.lock (waits if held). The lock
is only ever removed by the process that created it. Server binds 127.0.0.1.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image

HOST = "127.0.0.1"
PORT = 1234
BASE_URL = f"http://{HOST}:{PORT}/v1"
MODEL_KEY = "qwen/qwen3.8-27b"
IDENTIFIER = "belfast-vlm"
CONTEXT_LENGTH = 16384
TTL_SECONDS = 1800
LMS = os.environ.get("LMS_EXE", r"C:\Users\Yordi\.lmstudio\bin\lms.exe")
LOCK_PATH = Path(r"H:\BelfastData\gpu.lock")
LOCK_POLL_S = 15
LOCK_TIMEOUT_S = 20 * 60
HTTP_TIMEOUT = (5, 300)  # connect, read
MAX_SIDE = 1280
JPEG_QUALITY = 88
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
# `reasoning_effort` is sent but this model/LM Studio build ignores it (always thinks ~220-350 tokens); see docs/spikes/vlm.md.
REASONING_LEVELS = ("low", "medium")


class VlmError(RuntimeError):
    pass


# ---------------------------------------------------------------- lms CLI

def run_lms(*args: str, timeout: float = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([LMS, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        raise VlmError(f"lms CLI not found at {LMS}") from e
    except subprocess.TimeoutExpired as e:
        raise VlmError(f"lms {' '.join(args)} timed out after {timeout}s") from e


def server_up(timeout: float = 3) -> bool:
    try:
        return requests.get(f"{BASE_URL}/models", timeout=timeout).ok
    except requests.RequestException:
        return False


def start_server() -> bool:
    """Start the local server on localhost only. Idempotent; True if started now."""
    if server_up():
        return False
    r = run_lms("server", "start", "--port", str(PORT), "--bind", HOST, timeout=120)
    if r.returncode != 0:
        raise VlmError(f"lms server start failed: {r.stdout.strip()} {r.stderr.strip()}")
    deadline = time.time() + 60
    while time.time() < deadline:
        if server_up():
            return True
        time.sleep(1)
    raise VlmError("server did not answer on 127.0.0.1:1234 within 60 s")


def loaded_models() -> list[str]:
    r = requests.get(f"{BASE_URL}/models", timeout=HTTP_TIMEOUT[0] + 10)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def is_loaded(identifier: str = IDENTIFIER) -> bool:
    ps = run_lms("ps", "--json", timeout=60)
    if ps.returncode == 0:
        try:
            return any(m.get("identifier") == identifier for m in json.loads(ps.stdout))
        except json.JSONDecodeError:
            pass
    return identifier in loaded_models()


# ---------------------------------------------------------------- GPU lock

def beamng_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace", timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any("beamng" in line.lower() for line in out.splitlines())


class GpuLock:
    """File lock with an owner tag. Releases only a lock this object created."""

    def __init__(self, path: Path = LOCK_PATH, timeout: float = LOCK_TIMEOUT_S,
                 poll: float = LOCK_POLL_S, owner: str | None = None):
        self.path = Path(path)
        self.timeout = timeout
        self.poll = poll
        self.owner = owner or f"vlm pid={os.getpid()} t={int(time.time())}"
        self.held = False

    def acquire(self) -> None:
        if self.held:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except (FileExistsError, PermissionError):  # PermissionError: Windows delete-pending
                if time.time() >= deadline:
                    raise VlmError(f"gpu lock {self.path} still held after {self.timeout}s: "
                                   f"{self._peek()}")
                time.sleep(min(self.poll, max(0.0, deadline - time.time())))
                continue
            with os.fdopen(fd, "w") as f:
                f.write(self.owner)
            self.held = True
            return

    def _peek(self) -> str:
        try:
            return self.path.read_text().strip()
        except OSError:
            return "?"

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        if self._peek() == self.owner:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()


# ---------------------------------------------------------------- images

def prepare_image(path_or_img, max_side: int = MAX_SIDE, quality: int = JPEG_QUALITY) -> tuple[bytes, tuple[int, int]]:
    """Downscale so the longest side <= max_side (never upscale); return JPEG bytes, (w, h)."""
    img = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img)
    img = img.convert("RGB")
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue(), img.size


# ---------------------------------------------------------------- schema

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON-schema check: type, required, properties, items, enum, minimum, maximum."""
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing required '{k}'")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += validate(value[k], sub, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


def parse_json_reply(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


# ---------------------------------------------------------------- requests

def build_request(jpeg: bytes, schema: dict, prompt: str, reasoning: str = "low",
                  model: str = IDENTIFIER, max_tokens: int = 4096) -> dict:
    if reasoning not in REASONING_LEVELS:
        raise VlmError(f"reasoning must be one of {REASONING_LEVELS}")
    uri = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    return {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": uri}},
        ]}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "answer", "strict": True, "schema": schema}},
        "reasoning_effort": reasoning,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }


def post_chat(payload: dict) -> dict:
    try:
        r = requests.post(f"{BASE_URL}/chat/completions", json=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        raise VlmError(f"chat request failed: {e}") from e
    if not r.ok:
        raise VlmError(f"chat HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def judge_image(image, schema: dict, prompt: str, reasoning: str = "low",
                max_side: int = MAX_SIDE, model: str = IDENTIFIER) -> dict:
    """Return {'result', 'tokens', 'seconds', 'reasoning', 'attempts', 'image'}.
    Retries once on invalid JSON or schema failure, then raises VlmError."""
    jpeg, size = prepare_image(image, max_side)
    payload = build_request(jpeg, schema, prompt, reasoning, model)
    last = ""
    total_tokens, t0 = 0, time.time()
    for attempt in (1, 2):
        data = post_chat(payload)
        usage = data.get("usage", {})
        total_tokens += usage.get("completion_tokens", 0)
        text = (data["choices"][0]["message"].get("content") or "")
        try:
            obj = parse_json_reply(text)
            errs = validate(obj, schema)
        except json.JSONDecodeError as e:
            obj, errs = None, [f"invalid JSON: {e}"]
        if not errs:
            return {"result": obj, "tokens": total_tokens, "prompt_tokens": usage.get("prompt_tokens"),
                    "seconds": round(time.time() - t0, 2), "reasoning": reasoning,
                    "attempts": attempt, "image": str(image) if not isinstance(image, Image.Image) else None,
                    "sent_size": list(size)}
        last = f"{errs} reply={text[:300]!r}"
    raise VlmError(f"model reply invalid after retry: {last}")


# ---------------------------------------------------------------- session

def load_model(wait_lock: bool = True, lock: GpuLock | None = None) -> GpuLock | None:
    """Idempotent load. Returns the lock held by this call (None if already loaded)."""
    start_server()
    if is_loaded():
        return None
    if beamng_running():
        raise VlmError("BeamNG is running; refusing to load the model (GPU must not be shared)")
    lock = lock or GpuLock()
    lock.acquire()
    try:
        if beamng_running():
            raise VlmError("BeamNG started while waiting for the lock; not loading")
        r = run_lms("load", MODEL_KEY, "--identifier", IDENTIFIER, "--context-length", str(CONTEXT_LENGTH),
                    "--gpu", "max", "--ttl", str(TTL_SECONDS), "-y", timeout=900)
        if r.returncode != 0:
            raise VlmError(f"lms load failed: {r.stdout.strip()} {r.stderr.strip()}")
    except BaseException:
        lock.release()
        raise
    return lock


def unload_model() -> None:
    run_lms("unload", "--all", timeout=120)


class Session:
    def __init__(self):
        self.lock: GpuLock | None = None

    def __enter__(self):
        self.lock = load_model()
        return self

    def __exit__(self, *exc):
        try:
            unload_model()
        finally:
            if self.lock:
                self.lock.release()
        return False

    def judge(self, image, schema, prompt, reasoning="low", **kw):
        return judge_image(image, schema, prompt, reasoning, **kw)


def session() -> Session:
    return Session()


# ---------------------------------------------------------------- CLI

def _schema(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("start", "load", "unload", "status"):
        sub.add_parser(name)
    j = sub.add_parser("judge")
    j.add_argument("image")
    b = sub.add_parser("batch")
    b.add_argument("dir")
    b.add_argument("--out", required=True)
    for s in (j, b):
        s.add_argument("--schema", required=True)
        s.add_argument("--prompt", required=True)
        s.add_argument("--reasoning", choices=REASONING_LEVELS, default="low")
        s.add_argument("--max-side", type=int, default=MAX_SIDE)
    a = p.parse_args(argv)
    try:
        if a.cmd == "start":
            print("started" if start_server() else "already running")
        elif a.cmd == "load":
            lock = load_model()
            # lock stays on disk after the CLI exits; unload (same machine) clears our own stale file
            if lock:
                lock.held = False  # hand over: removed by `unload`
            print("loaded" if lock else "already loaded")
        elif a.cmd == "unload":
            unload_model()
            if LOCK_PATH.exists() and LOCK_PATH.read_text().startswith("vlm "):
                LOCK_PATH.unlink()
            print("unloaded")
        elif a.cmd == "status":
            up = server_up()
            print(json.dumps({"server": up, "loaded": is_loaded() if up else False,
                              "beamng": beamng_running(), "lock": LOCK_PATH.exists()}))
        elif a.cmd == "judge":
            out = judge_image(a.image, _schema(a.schema), a.prompt, a.reasoning, a.max_side)
            print(json.dumps(out["result"]))
            print(f"# {out['tokens']} tokens, {out['seconds']} s", file=sys.stderr)
        elif a.cmd == "batch":
            schema = _schema(a.schema)
            files = sorted(f for f in Path(a.dir).iterdir() if f.suffix.lower() in IMAGE_EXTS)
            with open(a.out, "w", encoding="utf-8") as fh:
                for f in files:
                    try:
                        out = judge_image(f, schema, a.prompt, a.reasoning, a.max_side)
                        row = {"file": f.name, **out}
                    except VlmError as e:
                        row = {"file": f.name, "error": str(e)}
                    row.pop("image", None)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
            print(f"{len(files)} frames -> {a.out}")
    except VlmError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
