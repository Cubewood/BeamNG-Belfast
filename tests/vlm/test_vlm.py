"""GPU-free tests for tools/vlm.py."""
import base64
import io
import json
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import vlm  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "n": {"type": "integer", "minimum": 0},
        "colour": {"type": "string", "enum": ["red", "blue"]},
        "conf": {"type": "number"},
    },
    "required": ["n", "colour"],
}


# ---- validator
def test_validate_ok():
    assert vlm.validate({"n": 2, "colour": "red", "conf": 0.5}, SCHEMA) == []


def test_validate_missing_required():
    assert any("missing required 'colour'" in e for e in vlm.validate({"n": 1}, SCHEMA))


def test_validate_enum_and_type():
    errs = vlm.validate({"n": "2", "colour": "pink"}, SCHEMA)
    assert len(errs) == 2


def test_validate_bool_is_not_integer():
    assert vlm.validate({"n": True, "colour": "red"}, SCHEMA)


def test_validate_minimum_and_items():
    assert vlm.validate({"n": -1, "colour": "red"}, SCHEMA)
    assert vlm.validate([1, "a"], {"type": "array", "items": {"type": "integer"}})


def test_parse_json_reply_fenced():
    assert vlm.parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}


# ---- images
def test_downscale_and_jpeg():
    jpeg, size = vlm.prepare_image(Image.new("RGB", (3840, 2160), "red"), max_side=1280)
    assert size == (1280, 720)
    assert jpeg[:2] == b"\xff\xd8"
    assert Image.open(io.BytesIO(jpeg)).size == (1280, 720)


def test_no_upscale_and_rgba(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGBA", (200, 100), (0, 0, 0, 0)).save(p)
    _, size = vlm.prepare_image(p, max_side=1280)
    assert size == (200, 100)


# ---- lock
def test_lock_acquire_release(tmp_path):
    lk = vlm.GpuLock(tmp_path / "gpu.lock", owner="me")
    lk.acquire()
    assert (tmp_path / "gpu.lock").read_text() == "me"
    lk.release()
    assert not (tmp_path / "gpu.lock").exists()


def test_lock_times_out_and_leaves_foreign_lock(tmp_path):
    path = tmp_path / "gpu.lock"
    path.write_text("T-005")
    lk = vlm.GpuLock(path, timeout=0.3, poll=0.1, owner="me")
    with pytest.raises(vlm.VlmError, match="T-005"):
        lk.acquire()
    lk.release()
    assert path.read_text() == "T-005"


def test_lock_waits_then_acquires(tmp_path):
    path = tmp_path / "gpu.lock"
    path.write_text("other")
    threading.Timer(0.3, path.unlink).start()
    lk = vlm.GpuLock(path, timeout=5, poll=0.1, owner="me")
    lk.acquire()
    assert path.read_text() == "me"
    lk.release()


def test_release_does_not_delete_replaced_lock(tmp_path):
    path = tmp_path / "gpu.lock"
    lk = vlm.GpuLock(path, owner="me")
    lk.acquire()
    path.write_text("someone else")
    lk.release()
    assert path.exists()


def test_load_refuses_when_beamng_running():
    with mock.patch.object(vlm, "start_server"), mock.patch.object(vlm, "is_loaded", return_value=False), \
            mock.patch.object(vlm, "beamng_running", return_value=True):
        with pytest.raises(vlm.VlmError, match="BeamNG"):
            vlm.load_model()


def test_session_unloads_and_releases_on_error(tmp_path):
    lk = vlm.GpuLock(tmp_path / "gpu.lock", owner="me")
    lk.acquire()
    with mock.patch.object(vlm, "load_model", return_value=lk), \
            mock.patch.object(vlm, "unload_model") as unload:
        with pytest.raises(ZeroDivisionError):
            with vlm.session():
                1 / 0
    unload.assert_called_once()
    assert not (tmp_path / "gpu.lock").exists()


# ---- request building and retry (mocked HTTP)
def test_build_request():
    req = vlm.build_request(b"\xff\xd8abc", SCHEMA, "count", "medium")
    content = req["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "count"}
    uri = content[1]["image_url"]["url"]
    assert uri.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == b"\xff\xd8abc"
    assert req["response_format"]["json_schema"]["schema"] == SCHEMA
    assert req["reasoning_effort"] == "medium"
    assert req["model"] == vlm.IDENTIFIER


def test_build_request_bad_reasoning():
    with pytest.raises(vlm.VlmError):
        vlm.build_request(b"x", SCHEMA, "p", "high")


def _reply(text):
    return {"choices": [{"message": {"content": text}}], "usage": {"completion_tokens": 7, "prompt_tokens": 100}}


def test_judge_retries_once_then_succeeds():
    img = Image.new("RGB", (64, 64))
    with mock.patch.object(vlm, "post_chat", side_effect=[_reply("not json"), _reply('{"n":1,"colour":"red"}')]) as m:
        out = vlm.judge_image(img, SCHEMA, "p")
    assert out["result"] == {"n": 1, "colour": "red"}
    assert out["attempts"] == 2 and out["tokens"] == 14
    assert m.call_count == 2


def test_judge_raises_after_second_failure():
    img = Image.new("RGB", (64, 64))
    with mock.patch.object(vlm, "post_chat", return_value=_reply('{"n":1,"colour":"pink"}')) as m:
        with pytest.raises(vlm.VlmError, match="invalid after retry"):
            vlm.judge_image(img, SCHEMA, "p")
    assert m.call_count == 2


def test_post_chat_uses_timeout():
    resp = mock.Mock(ok=True)
    resp.json.return_value = {}
    with mock.patch.object(vlm.requests, "post", return_value=resp) as m:
        vlm.post_chat({"a": 1})
    assert m.call_args.kwargs["timeout"] == vlm.HTTP_TIMEOUT
    assert "127.0.0.1" in m.call_args.args[0]
