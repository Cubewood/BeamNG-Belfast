import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import taskfile as tf  # noqa: E402
import next_task  # noqa: E402
import task_state  # noqa: E402

HEADER = "| ID | Status | Task | Model | Effort | Where | Needs | Done when |\n|---|---|---|---|---|---|---|---|\n"


def row(i, status="todo", model="sonnet", where="any", needs="-", task="do it"):
    return f"| {i} | {status} | {task} | {model} | medium | {where} | {needs} | ok |\n"


def make(tmp_path, inbox="", m0="", m1="", eol="\n"):
    text = (
        "# Plan\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## 7. Milestones and tasks\n\nintro\n\n"
        f"### Inbox\n\n{HEADER}{inbox}\n### M0: Foundations\n\n{HEADER}{m0}\n"
        f"### M1: Next\n\n{HEADER}{m1}\n## 8. Rules\n\n| x | y |\n|---|---|\n| T-999 | todo |\n"
    )
    p = tmp_path / "task.md"
    p.write_bytes(text.replace("\n", eol).encode("utf-8"))
    return p


def nxt(p, mode, capsys, *extra):
    code = next_task.main([mode, "--file", str(p), *extra])
    return code, capsys.readouterr().out


def test_ready_no_needs(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1"))
    code, out = nxt(p, "day", capsys)
    assert code == 0
    d = json.loads(out)
    assert d["id"] == "T-1" and d["needs"] == [] and set(d) == set(tf.FIELDS)


def test_needs_satisfied_and_unsatisfied(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1", "doing") + row("T-2", needs="T-1") + row("T-3", "done") + row("T-4", needs="T-3"))
    code, out = nxt(p, "day", capsys)
    assert json.loads(out)["id"] == "T-4"


def test_multiple_needs(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1", "done") + row("T-2", "todo", where="game") + row("T-3", needs="T-1, T-2"))
    assert nxt(p, "night", capsys)[1].count("T-3") == 0 or json.loads(nxt(p, "night", capsys)[1])["id"] != "T-3"
    task_state.main(["T-2", "done", "--file", str(p)])
    capsys.readouterr()
    assert json.loads(nxt(p, "night", capsys)[1])["id"] == "T-3"


def test_unknown_need_is_not_ready(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1", needs="T-77"))
    assert nxt(p, "day", capsys) == (3, "")


def test_day_vs_night(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1", where="game") + row("T-2", where="gpu") + row("T-3"))
    assert json.loads(nxt(p, "day", capsys)[1])["id"] == "T-3"
    assert json.loads(nxt(p, "night", capsys)[1])["id"] == "T-1"
    code = next_task.main(["--list-ready", "night", "--file", str(p)])
    lines = capsys.readouterr().out.splitlines()
    assert [l.split("\t")[0] for l in lines] == ["T-1", "T-2", "T-3"]


def test_you_rows_skipped(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1", model="you", where="you") + row("T-2", model="you", where="any")
             + row("T-3", model="sonnet", where="you") + row("T-4"))
    for mode in ("day", "night"):
        assert json.loads(nxt(p, mode, capsys)[1])["id"] == "T-4"
    next_task.main(["--you", "--file", str(p)])
    ids = [l.split("\t")[0] for l in capsys.readouterr().out.splitlines()]
    assert ids == ["T-1", "T-2", "T-3"]


def test_inbox_priority(tmp_path, capsys):
    p = make(tmp_path, inbox=row("T-900"), m0=row("T-1"))
    assert json.loads(nxt(p, "day", capsys)[1])["id"] == "T-900"


def test_file_order_across_milestones(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1", "done"), m1=row("T-2") + row("T-3"))
    assert json.loads(nxt(p, "day", capsys)[1])["id"] == "T-2"


def test_nothing_ready_exit_3(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1", "doing") + row("T-2", "blocked") + row("T-3", "done") + row("T-4", model="you", where="you"))
    assert nxt(p, "day", capsys) == (3, "")
    assert nxt(p, "night", capsys) == (3, "")


def test_section8_table_ignored(tmp_path, capsys):
    p = make(tmp_path)
    assert nxt(p, "day", capsys) == (3, "")


def test_md_format(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1"))
    out = nxt(p, "day", capsys, "--format", "md")[1]
    assert out == row("T-1")


def test_cli_subprocess_exit_code(tmp_path):
    p = make(tmp_path)
    r = subprocess.run([sys.executable, str(TOOLS / "next_task.py"), "day", "--file", str(p)], capture_output=True, text=True)
    assert r.returncode == 3 and r.stdout == ""


# ---- task_state ----

def test_round_trip_changes_one_cell(tmp_path):
    p = make(tmp_path, m0=row("T-1") + row("T-2"))
    before = p.read_bytes()
    assert task_state.main(["T-1", "doing", "--file", str(p)]) == 0
    after = p.read_bytes()
    assert after == before.replace(b"| T-1 | todo |", b"| T-1 | doing |")
    assert task_state.main(["T-1", "todo", "--file", str(p)]) == 0
    assert p.read_bytes() == before


def test_crlf_preserved(tmp_path):
    p = make(tmp_path, m0=row("T-1"), eol="\r\n")
    before = p.read_bytes()
    task_state.main(["T-1", "done", "--note", "ok", "--file", str(p)])
    after = p.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n")
    assert after.count(b"\n") == after.count(b"\r\n")


def test_note_with_pipe(tmp_path):
    p = make(tmp_path, m0=row("T-1") + row("T-2"))
    task_state.main(["T-1", "blocked", "--note", "a|b", "--file", str(p)])
    rows = {r.id: r for r in tf.load(p)}
    assert rows["T-1"].status == "blocked"
    assert rows["T-1"].task == "do it (note: a/b)"
    assert len(rows["T-1"].cells) == 8
    assert rows["T-1"].done_when == "ok"
    assert rows["T-2"].status == "todo"


def test_unknown_id_and_invalid_status(tmp_path, capsys):
    p = make(tmp_path, m0=row("T-1"))
    before = p.read_bytes()
    assert task_state.main(["T-404", "done", "--file", str(p)]) != 0
    assert "unknown" in capsys.readouterr().err
    assert task_state.main(["T-1", "finished", "--file", str(p)]) != 0
    assert "invalid status" in capsys.readouterr().err
    assert p.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_state_ignores_section8_ids(tmp_path):
    p = make(tmp_path, m0=row("T-1"))
    assert task_state.main(["T-999", "done", "--file", str(p)]) != 0


# ---- real task.md (read-only, stays true as statuses change) ----

REAL = ROOT / "task.md"


@pytest.fixture(scope="module")
def real_rows():
    return tf.load(REAL)


def test_real_parses(real_rows):
    assert len(real_rows) > 10


def test_real_eight_cells(real_rows):
    for r in real_rows:
        assert len(r.cells) == 8, r.id


def test_real_ids_unique(real_rows):
    ids = [r.id for r in real_rows]
    assert len(ids) == len(set(ids))


def test_real_needs_exist(real_rows):
    ids = {r.id for r in real_rows}
    for r in real_rows:
        for n in r.needs_list:
            assert n in ids, f"{r.id} needs unknown {n}"


def test_real_values_valid(real_rows):
    for r in real_rows:
        assert r.status in tf.STATUSES, r.id
        assert r.where in tf.WHERES, r.id
        assert r.model in ("haiku", "sonnet", "opus", "fable", "you"), r.id


def test_real_cli_runs_and_is_consistent():
    for mode in ("day", "night"):
        r = subprocess.run([sys.executable, str(TOOLS / "next_task.py"), mode], capture_output=True, text=True)
        assert r.returncode in (0, 3)
        if r.returncode == 0:
            d = json.loads(r.stdout)
            assert d["status"] == "todo" and d["where"] != "you" and d["model"] != "you"
            if mode == "day":
                assert d["where"] == "any"
        else:
            assert r.stdout == ""
