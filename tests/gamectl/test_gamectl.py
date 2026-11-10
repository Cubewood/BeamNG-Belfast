"""Game-free tests for tools/gamectl.py and tools/beamng_mcp.py."""
import base64
import datetime as dt
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import beamng_mcp  # noqa: E402
import gamectl  # noqa: E402


# ---------------------------------------------------------------- daytime window

@pytest.mark.parametrize("hh,mm,expected", [
    (8, 59, False), (9, 0, True), (12, 30, True), (17, 59, True), (18, 0, False), (23, 0, False), (0, 5, False),
])
def test_daytime_window(hh, mm, expected):
    assert gamectl.is_daytime(dt.datetime(2026, 11, 10, hh, mm)) is expected


# ---------------------------------------------------------------- lock handling

def test_lock_exclusive_and_release_only_if_ours(tmp_path):
    p = tmp_path / "gpu.lock"
    a, b = gamectl.GameLock(p, "game pid=1 t=1"), gamectl.GameLock(p, "game pid=2 t=2")
    assert a.try_acquire()
    assert not b.try_acquire()
    assert p.read_text() == "game pid=1 t=1"
    assert b.release() is False and p.exists()          # b never held it
    assert a.release() is True and not p.exists()


def test_release_does_not_remove_someone_elses_lock(tmp_path):
    p = tmp_path / "gpu.lock"
    a = gamectl.GameLock(p, "game pid=1 t=1")
    assert a.try_acquire()
    p.write_text("vlm pid=9 t=9")                        # someone replaced it
    assert a.release() is False
    assert p.read_text() == "vlm pid=9 t=9"


def test_set_owner_swaps_line_and_release_matches_new_line(tmp_path):
    p = tmp_path / "gpu.lock"
    a = gamectl.GameLock(p, "game pid=1 t=1")
    a.try_acquire()
    a.set_owner("game pid=555 t=2")
    assert p.read_text() == "game pid=555 t=2"
    assert a.release() and not p.exists()


def test_acquire_waits_then_times_out_for_vlm_lock(tmp_path):
    p = tmp_path / "gpu.lock"
    p.write_text("vlm pid=424242 t=1")
    lk = gamectl.GameLock(p)
    clock = iter(range(0, 1000, 10))
    sleeps = []
    waits = []
    with mock.patch.object(gamectl, "get_proc_info", return_value=None):  # owner pid dead
        with pytest.raises(gamectl.GamectlError) as e:
            lk.acquire(timeout=35, poll=15, sleep=sleeps.append, clock=lambda: next(clock), on_wait=waits.append)
    assert e.value.code == gamectl.EXIT_LOCK_TIMEOUT
    assert p.read_text() == "vlm pid=424242 t=1"          # a dead vlm lock is NOT auto-cleared
    assert sleeps and waits == ["vlm pid=424242 t=1"] * len(waits)


def test_acquire_after_lock_clears(tmp_path):
    p = tmp_path / "gpu.lock"
    p.write_text("vlm pid=7 t=1")
    lk = gamectl.GameLock(p, "game pid=1 t=1")
    calls = []

    def fake_sleep(_):
        calls.append(1)
        p.unlink()                                       # the model's owner releases it

    lk.acquire(timeout=100, poll=15, sleep=fake_sleep)
    assert lk.held and p.read_text() == "game pid=1 t=1" and calls == [1]


def test_stale_game_lock_is_removed_on_acquire(tmp_path):
    p = tmp_path / "gpu.lock"
    p.write_text("game pid=31337 t=1")
    lk = gamectl.GameLock(p, "game pid=1 t=2")
    with mock.patch.object(gamectl, "get_proc_info", return_value=None):
        lk.acquire(timeout=5, poll=1, sleep=lambda s: None)
    assert p.read_text() == "game pid=1 t=2"


def test_alive_game_lock_is_respected(tmp_path):
    p = tmp_path / "gpu.lock"
    p.write_text("game pid=31337 t=1")
    lk = gamectl.GameLock(p)
    with mock.patch.object(gamectl, "get_proc_info", return_value=gamectl.ProcInfo(31337, 1.0, "x")):
        with pytest.raises(gamectl.GamectlError):
            lk.acquire(timeout=0, poll=1, sleep=lambda s: None)


def test_parse_owner_and_stale_detection():
    assert gamectl.parse_owner("vlm pid=12 t=34") == {"kind": "vlm", "pid": 12, "t": 34}
    assert gamectl.parse_owner("garbage") is None
    assert gamectl.lock_is_stale("game pid=5 t=1", pid_alive=lambda p: False) is True
    assert gamectl.lock_is_stale("game pid=5 t=1", pid_alive=lambda p: True) is False
    assert gamectl.lock_is_stale("garbage", pid_alive=lambda p: False) is False   # never guess
    assert gamectl.lock_is_stale(None) is False


def test_release_lock_if_game_checks_pid(tmp_path):
    p = tmp_path / "gpu.lock"
    p.write_text("game pid=100 t=1")
    assert gamectl.release_lock_if_game(200, p) is False and p.exists()
    p.write_text("vlm pid=100 t=1")
    assert gamectl.release_lock_if_game(100, p) is False and p.exists()
    p.write_text("game pid=100 t=1")
    assert gamectl.release_lock_if_game(100, p) is True and not p.exists()


def test_state_file_roundtrip(tmp_path):
    p = tmp_path / "game.state.json"
    assert gamectl.read_state(p) is None
    gamectl.write_state({"pid": 5, "start_time": 1.5}, p)
    assert gamectl.read_state(p) == {"pid": 5, "start_time": 1.5}
    p.write_text("{not json")
    assert gamectl.read_state(p) == {"corrupt": True}
    gamectl.remove_state(p)
    assert not p.exists()
    gamectl.remove_state(p)                              # idempotent


# ---------------------------------------------------------------- PID-reuse guard

EXE = r"H:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe"


def test_same_process_accepts_recorded_beamng():
    assert gamectl.same_process(1000.0, gamectl.ProcInfo(7, 1000.5, EXE))


@pytest.mark.parametrize("info", [
    None,                                                        # dead
    gamectl.ProcInfo(7, 1000.0, r"C:\Windows\notepad.exe"),     # pid reused by another program
    gamectl.ProcInfo(7, 5000.0, EXE),                            # pid reused by another BeamNG (start differs)
    gamectl.ProcInfo(7, None, None),                             # access denied: identity unknown
])
def test_same_process_rejects(info):
    assert not gamectl.same_process(1000.0, info)


def test_same_process_needs_recorded_start():
    assert not gamectl.same_process(None, gamectl.ProcInfo(7, 1000.0, EXE))


def test_is_beamng_image_is_case_insensitive_exact_name():
    assert gamectl.is_beamng_image(EXE.upper())
    assert not gamectl.is_beamng_image(r"C:\x\BeamNG.drive.exe")
    assert not gamectl.is_beamng_image(None)


def test_terminate_if_same_refuses_dead_pid():
    # pid 0xFFFFFF0 does not exist: nothing to open, nothing killed
    assert gamectl.terminate_if_same(0xFFFFFF0, 1.0) is False


def test_get_proc_info_of_own_process():
    info = gamectl.get_proc_info(os.getpid())
    assert info and info.image and Path(info.image).name.lower().startswith("python")
    assert abs(info.start - __import__("time").time()) < 24 * 3600 * 365
    assert not gamectl.is_beamng_image(info.image)


# ---------------------------------------------------------------- already-running refusal

def test_start_refuses_when_any_beamng_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "list_beamng_processes", lambda: [("BeamNG.drive.x64.exe", 4242)])
    popen = mock.Mock()
    monkeypatch.setattr(gamectl.subprocess, "Popen", popen)
    monkeypatch.setattr(gamectl, "LOCK_PATH", tmp_path / "gpu.lock")
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    with pytest.raises(gamectl.GamectlError) as e:
        gamectl.cmd_start(allow_daytime=True)
    assert e.value.code == gamectl.EXIT_RUNNING and "4242" in e.value.message
    popen.assert_not_called()
    assert not (tmp_path / "gpu.lock").exists() and not (tmp_path / "state.json").exists()


def test_start_refuses_in_daytime(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "list_beamng_processes", lambda: [])
    monkeypatch.setattr(gamectl, "is_daytime", lambda now=None: True)
    popen = mock.Mock()
    monkeypatch.setattr(gamectl.subprocess, "Popen", popen)
    monkeypatch.setattr(gamectl, "LOCK_PATH", tmp_path / "gpu.lock")
    with pytest.raises(gamectl.GamectlError) as e:
        gamectl.cmd_start(allow_daytime=False)
    assert e.value.code == gamectl.EXIT_DAYTIME
    popen.assert_not_called()
    assert not (tmp_path / "gpu.lock").exists()


def test_start_exit_code_through_cli(monkeypatch, capsys):
    monkeypatch.setattr(gamectl, "list_beamng_processes", lambda: [("BeamNG.drive.exe", 1)])
    assert gamectl.main(["start", "--allow-daytime"]) == gamectl.EXIT_RUNNING
    assert "already running" in capsys.readouterr().err


def test_parse_tasklist_csv():
    text = '"System","4","Services","0","12 K"\n"BeamNG.drive.x64.exe","777","Console","1","2,000,000 K"\n'
    assert gamectl.parse_tasklist_csv(text) == [("System", 4), ("BeamNG.drive.x64.exe", 777)]


def test_list_beamng_processes_filters_by_name(monkeypatch):
    out = ('"svchost.exe","8","Services","0","1 K"\n"BeamNG.drive.exe","10","Console","1","1 K"\n'
           '"beamng.drive.x64.exe","11","Console","1","1 K"\n')
    monkeypatch.setattr(gamectl.subprocess, "run", lambda *a, **k: mock.Mock(stdout=out))
    assert gamectl.list_beamng_processes() == [("BeamNG.drive.exe", 10), ("beamng.drive.x64.exe", 11)]


def test_launch_args_never_contain_remote_mcp(monkeypatch, tmp_path):
    """Run the start path up to Popen with everything mocked and inspect the command line."""
    monkeypatch.setattr(gamectl, "list_beamng_processes", lambda: [])
    monkeypatch.setattr(gamectl, "is_daytime", lambda now=None: False)
    monkeypatch.setattr(gamectl, "LOCK_PATH", tmp_path / "gpu.lock")
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gamectl, "port_open", lambda *a, **k: False)
    monkeypatch.setattr(gamectl, "QA_ROOT", tmp_path)
    monkeypatch.setattr(gamectl, "EXE", Path(__file__))          # any existing file
    seen = {}

    def fake_popen(args, **kw):
        seen["args"], seen["kw"] = args, kw
        raise OSError("stop here")

    monkeypatch.setattr(gamectl.subprocess, "Popen", fake_popen)
    with pytest.raises(OSError):
        gamectl.cmd_start(allow_daytime=True, profile=tmp_path / "profile")
    assert "-enablemcp" in seen["args"] and "-userpath" in seen["args"]
    assert not any("mcpremote" in a for a in seen["args"])
    assert not any(a in ("-windowed", "-fullscreen", "-headless") for a in seen["args"])
    assert seen["kw"]["cwd"] == str(gamectl.INSTALL_DIR)
    assert not (tmp_path / "gpu.lock").exists()                   # released on failure


# ---------------------------------------------------------------- settings merge

def test_merge_preserves_unknown_keys_and_reports_changes(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"keep": [1, 2], "AudioMasterVol": 1.0, "x": {"y": 1}}))
    changed = gamectl.merge_json_file(f, {"AudioMasterVol": 0.0, "new": True})
    data = json.loads(f.read_text())
    assert sorted(changed) == ["AudioMasterVol", "new"]
    assert data == {"keep": [1, 2], "AudioMasterVol": 0.0, "x": {"y": 1}, "new": True}


def test_merge_is_noop_when_already_set(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"a": 1}')
    before = f.stat().st_mtime_ns
    assert gamectl.merge_json_file(f, {"a": 1}) == []
    assert f.stat().st_mtime_ns == before


def test_merge_creates_missing_file_and_dirs(tmp_path):
    f = tmp_path / "cloud" / "settings.json"
    changed = gamectl.merge_json_file(f, {"telemetry": "disable"})
    assert changed == ["telemetry"] and json.loads(f.read_text()) == {"telemetry": "disable"}


def test_merge_moves_corrupt_file_aside(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{oops")
    gamectl.merge_json_file(f, {"a": 1})
    assert json.loads(f.read_text()) == {"a": 1}
    assert (tmp_path / "s.json.corrupt").read_text() == "{oops"


def test_seed_settings_only_inside_qa_root(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "QA_ROOT", tmp_path / "qa")
    with pytest.raises(gamectl.GamectlError):
        gamectl.seed_settings(tmp_path / "elsewhere" / "profile")
    with pytest.raises(gamectl.GamectlError):
        gamectl.seed_settings(Path(os.environ.get("LOCALAPPDATA", str(tmp_path))) / "BeamNG" / "BeamNG.drive")
    prof = tmp_path / "qa" / "profile"
    res = gamectl.seed_settings(prof)
    s = json.loads((prof / "current" / "settings" / "settings.json").read_text())
    c = json.loads((prof / "current" / "settings" / "cloud" / "settings.json").read_text())
    assert s["AudioMasterVol"] == 0.0 and s["fpsLimitBackgroundEnabled"] is False
    assert s["GraphicDisplayResolutions"] == "1280 720" and s["GraphicDisplayModes"] == "Window"
    assert c == {"onboardingPopupShown": True, "showedInputLayoutPopupV37": True,
                 "onlineFeatures": "disable", "telemetry": "disable"}
    assert set(res) == {"settings.json", "cloud/settings.json"}
    assert gamectl.seed_settings(prof) == {"settings.json": [], "cloud/settings.json": []}   # idempotent


def test_archive_crash_reports_moves_them(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "QA_ROOT", tmp_path)
    cr = tmp_path / "profile" / "current" / "temp" / "crashReports"
    cr.mkdir(parents=True)
    (cr / "Logs").mkdir()
    (cr / "~CrashRpt.ini").write_text("x")
    dest = gamectl.archive_crash_reports(tmp_path / "profile")
    assert dest and (Path(dest) / "~CrashRpt.ini").exists()
    assert cr.is_dir() and not any(cr.iterdir())
    assert gamectl.archive_crash_reports(tmp_path / "profile") is None


def test_lua_literal():
    assert gamectl.lua_literal(True) == "true" and gamectl.lua_literal(False) == "false"
    assert gamectl.lua_literal(0.0) == "0.0" and gamectl.lua_literal("1280 720") == "'1280 720'"
    assert gamectl.lua_literal("it's") == "'it\\'s'"


def test_values_match():
    assert gamectl.values_match(0.0, 0) and gamectl.values_match(False, False)
    assert not gamectl.values_match(False, 0) and not gamectl.values_match(True, None)
    assert gamectl.values_match("a", "a") and not gamectl.values_match("a", None)


class FakeClient:
    def __init__(self, first, second):
        self.replies, self.lua = [first, second], []

    def run_lua_json(self, code, timeout=None):
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def run_lua(self, code, timeout=None):
        self.lua.append(code)
        return "nil"


def test_verify_settings_marks_preseed_live_failed(monkeypatch):
    monkeypatch.setattr(gamectl.time, "sleep", lambda s: None)
    good = dict(gamectl.QA_EXPECTED)
    c = FakeClient(good, good)
    rep = gamectl.verify_settings(c)
    assert all(r["ok"] and r["method"] == "preseed" for r in rep.values()) and c.lua == []
    first = dict(good, fpsLimitBackgroundEnabled=True)
    c = FakeClient(first, good)
    rep = gamectl.verify_settings(c)
    assert rep["fpsLimitBackgroundEnabled"]["method"] == "live" and rep["fpsLimitBackgroundEnabled"]["ok"]
    assert c.lua == ["settings.setValue('fpsLimitBackgroundEnabled', false)"]
    stuck = dict(good, telemetry="enable")
    rep = gamectl.verify_settings(FakeClient(stuck, stuck))
    assert rep["telemetry"]["method"] == "FAILED" and not rep["telemetry"]["ok"]


# ---------------------------------------------------------------- netstat / bind

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1132
  TCP    127.0.0.1:29292        0.0.0.0:0              LISTENING       5555
  TCP    127.0.0.1:50000        127.0.0.1:29292        ESTABLISHED     6000
  TCP    127.0.0.1:29292        127.0.0.1:50000        ESTABLISHED     5555
  TCP    127.0.0.1:55208        127.0.0.1:29292        TIME_WAIT       0
"""

LISTEN = "  TCP    {addr}:29292        0.0.0.0:0              LISTENING       {pid}"
FIN = "  TCP    127.0.0.1:29292        0.0.0.0:0              FIN_WAIT_1      5555"


def test_parse_netstat_and_loopback_ok():
    rows = gamectl.parse_netstat(NETSTAT)
    assert len(rows) == 5 and rows[1]["foreign"] == "0.0.0.0:0"
    verdict, ev = gamectl.evaluate_bind(rows, 29292, 5555)
    assert verdict == "good" and len(ev) == 2 and all("127.0.0.1:29292" in e for e in ev)  # listener + accepted socket
    assert [r["port"] for r in gamectl.rows_on_port(29292, rows)] == [29292, 29292]       # TIME_WAIT (pid 0) ignored


def test_bind_rejects_any_address_and_wrong_pid():
    for text in (LISTEN.format(addr="0.0.0.0", pid=5555), LISTEN.format(addr="[::]", pid=5555),
                 LISTEN.format(addr="127.0.0.1", pid=9)):
        rows = gamectl.parse_netstat(text)
        assert gamectl.evaluate_bind(rows, 29292, 5555)[0] == "bad"
        assert gamectl.check_bind(29292, 5555, sampler=lambda r=rows: r, sleep=lambda s: None)[0] is False
    both = gamectl.parse_netstat(LISTEN.format(addr="0.0.0.0", pid=5555) + chr(10) +
                                 LISTEN.format(addr="127.0.0.1", pid=5555))
    assert gamectl.evaluate_bind(both, 29292, 5555)[0] == "bad"


def test_bind_accepts_any_state_on_loopback():
    """Listener rows show odd states on this Windows build (FIN_WAIT_1, CLOSED, state 0)."""
    ticks = iter(x * 0.01 for x in range(1000))
    rows = gamectl.parse_netstat(FIN)
    ok, ev = gamectl.check_bind(29292, 5555, sampler=lambda: rows, sleep=lambda s: None,
                                clock=lambda: next(ticks), observe=0.5)
    assert ok and len(ev) == 1 and "FIN_WAIT_1" in ev[0]


def test_bind_fails_if_a_wildcard_row_shows_up_while_observing():
    good = gamectl.parse_netstat(FIN)
    wild = gamectl.parse_netstat(LISTEN.format(addr="0.0.0.0", pid=5555))
    seq = iter([good, good, wild])
    ticks = iter(x * 0.01 for x in range(1000))
    ok, ev = gamectl.check_bind(29292, 5555, sampler=lambda: next(seq), sleep=lambda s: None,
                                clock=lambda: next(ticks), observe=1.0)
    assert ok is False and "0.0.0.0:29292" in ev[0]


def test_bind_waits_for_socket_then_gives_up():
    snaps = iter([[], [], gamectl.parse_netstat(FIN)])
    ticks = iter(x * 0.01 for x in range(1000))
    ok, _ = gamectl.check_bind(29292, 5555, sampler=lambda: next(snaps), sleep=lambda s: None,
                               clock=lambda: next(ticks), observe=0.0)
    assert ok
    ticks = iter(range(0, 100))
    ok, ev = gamectl.check_bind(29292, 5555, sampler=lambda: [], window=3, sleep=lambda s: None,
                                clock=lambda: next(ticks))
    assert ok is False and "within 3 s" in ev[0]


def test_tcp_rows_reads_real_table_and_sees_a_listener():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        rows = gamectl.rows_on_port(port)
        # state is unreliable on this Windows build (a plain listener shows as state 0): only addr/pid/foreign count
        assert [(r["addr"], r["foreign"], r["pid"]) for r in rows] == [("127.0.0.1", "0.0.0.0:0", os.getpid())]
        assert gamectl.evaluate_bind(gamectl.tcp_rows(), port, os.getpid())[0] == "good"
        assert gamectl.evaluate_bind(gamectl.tcp_rows(), port, os.getpid() + 1)[0] == "bad"
    finally:
        srv.close()
    assert gamectl.rows_on_port(port) == []


# ---------------------------------------------------------------- log helpers

LOG = """  0.00133|D|logging| -- Log started
  0.49061|E|OnlineServiceProvider| Could not initialize online service provider: Steam
  2.46082|W|d3d12| swap chain init differs
 22.60032|W|GELua.ui_apps.| getUIAppsData() took too long
 23.00000|I|x| error word in an info line
"""


def test_parse_log_lines_levels_and_ignore():
    lines = LOG.splitlines()
    assert len(gamectl.parse_log_lines(lines, ("E",))) == 1
    assert len(gamectl.parse_log_lines(lines, ("W",))) == 2
    assert gamectl.parse_log_lines(lines, ("E", "W"), ignore=["Steam", "d3d12"]) == [lines[3].rstrip()]


def test_log_errors_reads_profile_log(tmp_path):
    (tmp_path / "current").mkdir()
    (tmp_path / "current" / "beamng.log").write_text(LOG)
    r = gamectl.log_errors(tmp_path)
    assert r["found"] and len(r["errors"]) == 1 and len(r["warnings"]) == 2
    assert gamectl.log_errors(tmp_path / "none")["found"] is False


# ---------------------------------------------------------------- screenshots

def _jpeg(color, size=(64, 36)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def test_image_stats_black_vs_real():
    black = gamectl.image_stats(_jpeg((0, 0, 0)))
    assert black["mean"] < 1 and not gamectl.screenshot_ok(black)
    img = Image.new("RGB", (64, 36), (10, 10, 10))
    for x in range(32):
        for y in range(36):
            img.putpixel((x, y), (200, 180, 160))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    real = gamectl.image_stats(buf.getvalue())
    assert real["mean"] > 50 and gamectl.screenshot_ok(real)
    assert (real["width"], real["height"]) == (64, 36)
    flat_grey = gamectl.image_stats(_jpeg((120, 120, 120)))
    assert not gamectl.screenshot_ok(flat_grey)          # uniform grey = no scene


def test_output_path_rules(tmp_path):
    outside = gamectl.check_output_path(tmp_path / "a.jpg")
    assert outside == Path(os.path.abspath(tmp_path / "a.jpg"))
    inside = gamectl.REPO_ROOT / "reports" / "shot.jpg"
    assert gamectl.check_output_path(inside, ignored=lambda p: True)
    with pytest.raises(gamectl.GamectlError):
        gamectl.check_output_path(inside, ignored=lambda p: False)


def test_real_gitignore_covers_jpg_and_reports():
    assert gamectl.is_ignored_by_git(gamectl.REPO_ROOT / "reports" / "x.jpg")
    assert not gamectl.is_ignored_by_git(gamectl.REPO_ROOT / "tools" / "gamectl.py")


# ---------------------------------------------------------------- level matching

def test_level_matches():
    assert gamectl.level_matches("/levels/gridmap_v2/info.json", "gridmap_v2")
    assert gamectl.level_matches("/levels/GridMap_v2/main.level.json", "gridmap_v2")
    assert not gamectl.level_matches("", "gridmap_v2") and not gamectl.level_matches(None, "gridmap_v2")
    assert not gamectl.level_matches("/levels/smallgrid/info.json", "gridmap_v2")


# ---------------------------------------------------------------- MCP client

class FakeResp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHttp:
    def __init__(self, *responses):
        self.responses, self.sent = list(responses), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_result(text, error=False):
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}], "isError": error}}


def test_build_request_shapes():
    assert beamng_mcp.build_request("ping", None, 3) == {"jsonrpc": "2.0", "method": "ping", "id": 3}
    assert beamng_mcp.build_request("notifications/initialized") == {"jsonrpc": "2.0", "method": "notifications/initialized"}
    p = beamng_mcp.tool_call_params("load_level", {"name": "smallgrid"})
    assert beamng_mcp.build_request("tools/call", p, 1)["params"] == {"name": "load_level", "arguments": {"name": "smallgrid"}}
    assert beamng_mcp.tool_call_params("get_status") == {"name": "get_status", "arguments": {}}


def test_client_posts_json_rpc_with_accept_header():
    http = FakeHttp(FakeResp(200, tool_result('{"level":""}')))
    c = beamng_mcp.McpClient(session=http)
    assert c.get_status() == {"level": ""}
    sent = http.sent[0]
    assert sent["url"] == "http://127.0.0.1:29292/mcp"
    assert sent["json"]["method"] == "tools/call" and sent["json"]["params"]["name"] == "get_status"
    assert "application/json" in sent["headers"]["Accept"]


def test_ids_increment_and_notifications_have_no_id():
    http = FakeHttp(FakeResp(200, {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}}),
                    FakeResp(202, text=""))
    c = beamng_mcp.McpClient(session=http)
    res = c.initialize()
    assert res["protocolVersion"] == "2025-11-25"
    assert http.sent[0]["json"]["id"] == 1 and http.sent[0]["json"]["params"]["protocolVersion"] == "2025-11-25"
    assert "id" not in http.sent[1]["json"] and http.sent[1]["json"]["method"] == "notifications/initialized"


def test_errors_are_mapped():
    c = beamng_mcp.McpClient(session=FakeHttp(requests.ConnectionError("down")))
    with pytest.raises(beamng_mcp.McpError):
        c.ping()
    c = beamng_mcp.McpClient(session=FakeHttp(FakeResp(405, text="no")))
    with pytest.raises(beamng_mcp.McpError):
        c.ping()
    c = beamng_mcp.McpClient(session=FakeHttp(FakeResp(200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601}})))
    with pytest.raises(beamng_mcp.McpError):
        c.ping()
    c = beamng_mcp.McpClient(session=FakeHttp(FakeResp(200, tool_result("bad args", error=True))))
    with pytest.raises(beamng_mcp.ToolError):
        c.call("load_level", {})
    c = beamng_mcp.McpClient(session=FakeHttp(FakeResp(200, tool_result("not json"))))
    with pytest.raises(beamng_mcp.McpError):
        c.get_status()


def test_run_lua_and_load_level_arguments():
    http = FakeHttp(FakeResp(200, tool_result("nil")), FakeResp(200, tool_result("loading level: x")))
    c = beamng_mcp.McpClient(session=http)
    c.run_lua("shutdown(0)")
    assert http.sent[0]["json"]["params"] == {"name": "run_lua", "arguments": {"code": "shutdown(0)"}}
    assert c.load_level("gridmap_v2").startswith("loading")
    assert http.sent[1]["json"]["params"] == {"name": "load_level", "arguments": {"name": "gridmap_v2"}}


def test_screenshot_polls_until_image():
    jpg = _jpeg((90, 90, 90))
    pending = tool_result("capturing JPG (scale 1.0); call screenshot_image again in a moment to receive the image")
    image = {"jsonrpc": "2.0", "id": 3, "result": {"content": [
        {"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(jpg).decode()}], "isError": False}}
    http = FakeHttp(FakeResp(200, pending), FakeResp(200, pending), FakeResp(200, image))
    c = beamng_mcp.McpClient(session=http)
    slept = []
    assert c.screenshot_image(scale=1.0, sleep=slept.append) == jpg
    assert len(http.sent) == 3 and len(slept) == 2
    assert http.sent[0]["json"]["params"]["arguments"] == {"scale": 1.0}


def test_screenshot_times_out():
    pending = tool_result("capturing JPG; call screenshot_image again")
    http = FakeHttp(*[FakeResp(200, pending) for _ in range(10)])
    c = beamng_mcp.McpClient(session=http)
    ticks = iter(range(0, 100, 20))
    with pytest.raises(beamng_mcp.McpError):
        c.screenshot_image(timeout=30, sleep=lambda s: None, clock=lambda: next(ticks))


# ---------------------------------------------------------------- stop (mocked game)

def test_stop_without_state_and_no_process_is_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gamectl, "list_beamng_processes", lambda: [])
    assert gamectl.cmd_stop()["stopped"] is False


def test_stop_without_state_refuses_foreign_beamng(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gamectl, "list_beamng_processes", lambda: [("BeamNG.drive.x64.exe", 9)])
    terminate = mock.Mock()
    monkeypatch.setattr(gamectl, "terminate_if_same", terminate)
    with pytest.raises(gamectl.GamectlError) as e:
        gamectl.cmd_stop()
    assert e.value.code == gamectl.EXIT_RUNNING
    terminate.assert_not_called()


def test_stop_with_reused_pid_cleans_files_and_kills_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gamectl, "LOCK_PATH", tmp_path / "gpu.lock")
    gamectl.write_state({"pid": 321, "start_time": 1000.0, "port": 29292}, tmp_path / "state.json")
    (tmp_path / "gpu.lock").write_text("game pid=321 t=1")
    # pid 321 now belongs to notepad: identity guard must reject it
    monkeypatch.setattr(gamectl, "get_proc_info", lambda pid: gamectl.ProcInfo(321, 1000.0, r"C:\Windows\notepad.exe"))
    terminate, shutdown = mock.Mock(), mock.Mock()
    monkeypatch.setattr(gamectl, "terminate_if_same", terminate)
    monkeypatch.setattr(gamectl.McpClient, "run_lua", shutdown)
    res = gamectl.cmd_stop()
    assert res["stopped"] is False and res["released_lock"] is True
    terminate.assert_not_called()
    shutdown.assert_not_called()
    assert not (tmp_path / "state.json").exists() and not (tmp_path / "gpu.lock").exists()


def test_stop_leaves_foreign_lock_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gamectl, "LOCK_PATH", tmp_path / "gpu.lock")
    gamectl.write_state({"pid": 321, "start_time": 1000.0}, tmp_path / "state.json")
    (tmp_path / "gpu.lock").write_text("vlm pid=321 t=1")
    monkeypatch.setattr(gamectl, "get_proc_info", lambda pid: None)
    res = gamectl.cmd_stop()
    assert res["released_lock"] is False and (tmp_path / "gpu.lock").exists()


def test_stop_falls_back_to_terminating_recorded_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gamectl, "LOCK_PATH", tmp_path / "gpu.lock")
    gamectl.write_state({"pid": 321, "start_time": 1000.0, "port": 29292}, tmp_path / "state.json")
    (tmp_path / "gpu.lock").write_text("game pid=321 t=1")
    alive = {"v": True}
    monkeypatch.setattr(gamectl, "get_proc_info", lambda pid: gamectl.ProcInfo(321, 1000.0, EXE) if alive["v"] else None)
    monkeypatch.setattr(gamectl.McpClient, "run_lua", lambda *a, **k: (_ for _ in ()).throw(beamng_mcp.McpError("down")))
    monkeypatch.setattr(gamectl, "port_open", lambda *a, **k: False)
    monkeypatch.setattr(gamectl, "wait_no_beamng", lambda *a, **k: [])
    calls = []

    def fake_terminate(pid, start, tol=2.0):
        calls.append((pid, start))
        alive["v"] = False
        return True

    monkeypatch.setattr(gamectl, "terminate_if_same", fake_terminate)
    # make the clean-quit wait fail fast
    real_wait = gamectl.wait_exit
    monkeypatch.setattr(gamectl, "wait_exit", lambda pid, start, timeout, poll=0.25: real_wait(pid, start, 0.3 if timeout > 10 else timeout, 0.05))
    res = gamectl.cmd_stop(timeout=60)
    assert res["method"] == "terminate-recorded-pid" and calls == [(321, 1000.0)]
    assert not (tmp_path / "gpu.lock").exists() and not (tmp_path / "state.json").exists()


# ---------------------------------------------------------------- clear-lock

def test_clear_lock_refuses_live_owner_and_vlm_and_removes_dead_game(tmp_path, monkeypatch):
    lock = tmp_path / "gpu.lock"
    monkeypatch.setattr(gamectl, "LOCK_PATH", lock)
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gamectl, "list_beamng_processes", lambda: [])
    lock.write_text("game pid=77 t=1")
    monkeypatch.setattr(gamectl, "get_proc_info", lambda pid: gamectl.ProcInfo(pid, 1.0, "x"))
    with pytest.raises(gamectl.GamectlError) as e:
        gamectl.cmd_clear_lock()
    assert e.value.code == gamectl.EXIT_LOCK_REFUSED and lock.exists()
    monkeypatch.setattr(gamectl, "get_proc_info", lambda pid: None)
    lock.write_text("vlm pid=77 t=1")
    with pytest.raises(gamectl.GamectlError):
        gamectl.cmd_clear_lock()
    assert lock.exists()
    assert gamectl.cmd_clear_lock(also_vlm=True)["removed"] and not lock.exists()
    lock.write_text("game pid=77 t=1")
    assert gamectl.cmd_clear_lock()["removed"] and not lock.exists()
    lock.write_text("???")
    with pytest.raises(gamectl.GamectlError):
        gamectl.cmd_clear_lock()


def test_clear_lock_no_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(gamectl, "LOCK_PATH", tmp_path / "gpu.lock")
    monkeypatch.setattr(gamectl, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gamectl, "list_beamng_processes", lambda: [])
    assert gamectl.cmd_clear_lock()["note"] == "no lock file"


def test_end_state_clean_logic():
    ok = {"beamng_processes": [], "port_closed": True, "state_file_gone": True, "game_lock_gone": True}
    assert gamectl.end_state_clean(ok)
    for k, v in (("beamng_processes", ["x:1"]), ("port_closed", False), ("state_file_gone", False), ("game_lock_gone", False)):
        assert not gamectl.end_state_clean({**ok, k: v})
