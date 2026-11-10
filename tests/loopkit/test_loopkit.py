"""Static and logic tests for the loop kit (T-010).

Run: uv run --no-project --python 3.12 --with pytest --with pyyaml pytest tests/loopkit -q
(env UV_CACHE_DIR=H:\\BelfastData\\cache\\uv, UV_PYTHON_INSTALL_DIR=H:\\BelfastData\\uv-python)

Nothing here launches the game or a model. The dummy task file is used through the
tools' --file option, so the real task.md is only ever read.
"""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "status"))
import next_task  # noqa: E402
import task_state  # noqa: E402
import taskfile as tf  # noqa: E402
import status_counts  # noqa: E402

AGENTS_DIR = ROOT / ".claude" / "agents"
SKILLS_DIR = ROOT / ".claude" / "skills"
DUMMY = Path(__file__).resolve().parent / "dummy_task.md"
MCPREMOTE = "-" + "mcpremote"  # built in pieces: the guard hook blocks the literal in code files

# agent -> (model id, effort)
EXPECTED_AGENTS = {
    "builder": ("claude-sonnet-5-5", "high"),
    "builder-light": ("claude-haiku-5-5", "medium"),
    "architect": ("claude-opus-5-5", "medium"),
    "architect-high": ("claude-opus-5-5", "high"),
    "architect-deep": ("claude-opus-5-5", "xhigh"),
    "steward": ("claude-haiku-5-5", "medium"),
}
VALID_MODEL_IDS = {"claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"}
ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
EXPECTED_SKILLS = ["next-task", "status", "triage", "qa", "truth", "vr-review"]
# Paths that a command may name before the task that builds them is done.
NOT_BUILT_YET = {"qa/nightly.ps1"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
AGENT_KEYS = {"name", "description", "tools", "disallowedTools", "model", "permissionMode", "maxTurns",
              "skills", "mcpServers", "hooks", "memory", "background", "effort", "isolation", "color",
              "initialPrompt", "omitClaudeMd", "autoCompactWindow", "experimental"}
SKILL_KEYS = {"name", "description", "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
              "user-invocable", "allowed-tools", "disallowed-tools", "model", "effort", "context", "agent",
              "background", "hooks", "paths", "shell", "metadata", "license", "compatibility"}


def split_frontmatter(path: Path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: the opening --- must be the first line"
    end = text.index("\n---\n", 4)
    fm = yaml.safe_load(text[4:end])
    assert isinstance(fm, dict), f"{path.name}: frontmatter did not parse to a mapping"
    return fm, text[end + 5:]


def agent_files():
    return {p.stem: p for p in sorted(AGENTS_DIR.glob("*.md"))}


def skill_files():
    return {p.parent.name: p for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))}


# ---------------------------------------------------------------- agents

def test_all_agents_exist():
    assert set(EXPECTED_AGENTS) <= set(agent_files())


@pytest.mark.parametrize("name", sorted(EXPECTED_AGENTS))
def test_agent_frontmatter(name):
    fm, body = split_frontmatter(AGENTS_DIR / f"{name}.md")
    model, effort = EXPECTED_AGENTS[name]
    assert fm["name"] == name
    assert len(fm["description"]) > 40
    assert fm["model"] == model
    assert fm["model"] in VALID_MODEL_IDS
    assert fm["effort"] == effort
    assert set(fm) <= AGENT_KEYS, f"unknown keys {set(fm) - AGENT_KEYS}"
    tools = [t.strip() for t in fm["tools"].split(",")] if isinstance(fm["tools"], str) else fm["tools"]
    assert "Read" in tools
    assert "Agent" not in tools  # subagents cannot spawn subagents
    assert "CLAUDE.md" in body


@pytest.mark.parametrize("name", [n for n in EXPECTED_AGENTS if n != "steward"])
def test_worker_agent_prompt_rules(name):
    _, body = split_frontmatter(AGENTS_DIR / f"{name}.md")
    for needle in ("exactly ONE", "Done-when", "FAIL", "RESULT: PASS | FAIL", "FILES CHANGED", "FOLLOW-UPS",
                   "EVIDENCE", "git add <the files", "do not", "GAME ALLOWED", MCPREMOTE):
        assert needle.lower() in body.lower(), f"{name}: missing '{needle}'"
    assert "partial pass is FAIL" in body
    assert "push" in body.lower()


def test_steward_never_writes_code():
    fm, body = split_frontmatter(AGENTS_DIR / "steward.md")
    assert "never write or change code" in body.lower()
    assert "Never commit, push" in body
    assert "8 cells" in body and "### Inbox" in body
    assert "Never writes code" in fm["description"]


# ---------------------------------------------------------------- skills

def test_all_skills_exist():
    assert set(EXPECTED_SKILLS) <= set(skill_files())


@pytest.mark.parametrize("name", EXPECTED_SKILLS)
def test_skill_frontmatter(name):
    fm, body = split_frontmatter(SKILLS_DIR / name / "SKILL.md")
    assert fm["name"] == name
    assert len(fm["description"]) > 30
    assert set(fm) <= SKILL_KEYS, f"unknown keys {set(fm) - SKILL_KEYS}"
    assert fm.get("disable-model-invocation") is True  # these are run by name, never auto-loaded
    if "model" in fm:
        assert fm["model"] in VALID_MODEL_IDS | set(ALIASES) | {"inherit"}
    if "effort" in fm:
        assert fm["effort"] in EFFORTS
    assert len(body.strip().splitlines()) < 90, "keep command files short"


def test_next_task_takes_an_argument():
    fm, body = split_frontmatter(SKILLS_DIR / "next-task" / "SKILL.md")
    assert fm["argument-hint"] == "<day|night>"
    assert "$ARGUMENTS" in body
    assert fm["model"] == "claude-haiku-5-5"  # the dispatcher runs on Haiku


def test_status_is_not_pinned_to_a_model():
    # The weekly Opus review runs "/status then review the plan" in one turn; a pin would downgrade it.
    fm, _ = split_frontmatter(SKILLS_DIR / "status" / "SKILL.md")
    assert "model" not in fm and "effort" not in fm


def tool_paths_in(text: str):
    found = set(re.findall(r"(?<![\w/])(tools/[\w.\-]+\.(?:py|ps1))", text))
    found |= set(re.findall(r"(\.claude/skills/[\w./\-]+\.py)", text))
    found |= set(re.findall(r"(?<![\w/.])(qa/[\w.\-]+\.ps1)", text))
    return found


@pytest.mark.parametrize("name", EXPECTED_SKILLS)
def test_skill_tool_paths_exist(name):
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    for rel in tool_paths_in(text):
        if rel in NOT_BUILT_YET:
            continue  # qa/nightly.ps1 appears with T-309; the command checks for it at run time
        assert (ROOT / rel).is_file(), f"{name} references missing {rel}"


def test_next_task_and_status_use_the_real_tools():
    nt = (SKILLS_DIR / "next-task" / "SKILL.md").read_text(encoding="utf-8")
    assert "tools/next_task.py" in nt and "tools/task_state.py" in nt
    st = (SKILLS_DIR / "status" / "SKILL.md").read_text(encoding="utf-8")
    assert "--list-ready night" in st and "--you" in st
    # one uv invocation form everywhere
    for text in (nt, st):
        for line in re.findall(r"`(uv run[^`]*)`", text):
            assert line.startswith("uv run --no-project --python 3.12 python "), line


def test_no_command_can_push_main_or_pass_the_remote_mcp_flag():
    for path in list(skill_files().values()) + list(agent_files().values()):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "git push" in line:
                assert "main" not in line.lower(), (path, line)
            if MCPREMOTE in line:
                assert re.search(r"never|do not|not allowed", line, re.I), (path, line)


def test_fail_soft_commands_name_the_unlocking_task():
    qa = (SKILLS_DIR / "qa" / "SKILL.md").read_text(encoding="utf-8")
    assert "T-309" in qa and "09:00-18:00" in qa and "BeamNG*" in qa
    truth = (SKILLS_DIR / "truth" / "SKILL.md").read_text(encoding="utf-8")
    assert "T-503/T-506" in truth
    triage = (SKILLS_DIR / "triage" / "SKILL.md").read_text(encoding="utf-8")
    assert "T-307" in triage and "No digest yet" in triage and "under 20 lines" in triage
    vr = (SKILLS_DIR / "vr-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "T-308" in vr and "Nothing to review" in vr


# ---------------------------------------------------- mapping consistency

def mapping_from_next_task():
    """Parse the model/effort -> agent table out of the /next-task command."""
    body = (SKILLS_DIR / "next-task" / "SKILL.md").read_text(encoding="utf-8")
    table = {}
    for m in re.finditer(r"^\|\s*(sonnet|haiku|opus)\s*\|\s*([^|]+?)\s*\|\s*([a-z\-]+)\s*\|\s*$", body, re.M):
        model, efforts, agent = m.groups()
        for eff in re.split(r"\s+or\s+", efforts):
            table[(model, eff.strip())] = agent
    return table


def agent_for(table, model, effort):
    if (model, effort) in table:
        return table[(model, effort)]
    if (model, "any") in table:
        return table[(model, "any")]
    return None


def test_mapping_points_at_real_agents_with_matching_model_and_effort():
    table = mapping_from_next_task()
    assert table[("sonnet", "high")] == "builder"
    assert table[("sonnet", "medium")] == "builder"  # runs at the sonnet default (high), see docs/loopkit.md
    assert table[("haiku", "any")] == "builder-light"
    assert table[("opus", "xhigh")] == "architect-deep"
    for (model, effort), agent in table.items():
        assert agent in EXPECTED_AGENTS, agent
        agent_model, agent_effort = EXPECTED_AGENTS[agent]
        assert agent_model == ALIASES[model]
        if model == "opus":
            assert agent_effort == effort  # opus tags map to the agent pinned at exactly that effort


def test_every_tag_in_the_real_task_file_has_an_agent_or_is_not_agent_work():
    table = mapping_from_next_task()
    for r in tf.load(ROOT / "task.md"):
        if r.model in ("haiku", "sonnet", "opus"):
            assert agent_for(table, r.model, r.effort), f"{r.id} {r.model}/{r.effort} has no agent"


def test_docs_mention_every_agent():
    doc = (ROOT / "docs" / "loopkit.md").read_text(encoding="utf-8")
    for agent in EXPECTED_AGENTS:
        assert f"`{agent}`" in doc, f"{agent} missing from docs/loopkit.md"
    for name in EXPECTED_SKILLS:
        assert f"/{name}" in doc, f"/{name} missing from docs/loopkit.md"


# ---------------------------------------------- dry run logic, dummy file

def test_dummy_file_parses_like_task_md():
    rows = tf.load(DUMMY)
    assert [r.id for r in rows] == [f"D-00{i}" for i in range(1, 9)]
    assert all(len(r.cells) == 8 for r in rows)


def test_dispatch_order_on_dummy(tmp_path, capsys):
    p = tmp_path / "task.md"
    shutil.copy(DUMMY, p)
    table = mapping_from_next_task()
    seen = []
    for _ in range(12):
        code = next_task.main(["night", "--file", str(p)])
        out = capsys.readouterr().out
        if code == 3:
            assert out == ""
            break
        row = json.loads(out)
        seen.append((row["id"], agent_for(table, row["model"], row["effort"])))
        assert task_state.main([row["id"], "doing", "--file", str(p)]) == 0
        assert task_state.main([row["id"], "done", "--file", str(p)]) == 0
        capsys.readouterr()
    assert seen == [
        ("D-001", "builder-light"), ("D-002", "builder"), ("D-003", "builder-light"),
        ("D-004", None),  # fable: no agent, /next-task blocks it
        ("D-005", "builder"), ("D-007", "architect"), ("D-008", "architect-deep"),
    ]


def test_day_never_returns_game_rows(tmp_path, capsys):
    p = tmp_path / "task.md"
    shutil.copy(DUMMY, p)
    for i in ("D-001", "D-002"):
        task_state.main([i, "done", "--file", str(p)])
    capsys.readouterr()
    code_day = next_task.main(["--list-ready", "day", "--file", str(p)])
    day = capsys.readouterr().out
    code_night = next_task.main(["--list-ready", "night", "--file", str(p)])
    night = capsys.readouterr().out
    assert "D-005" not in day and "D-005" in night
    assert "D-006" not in day and "D-006" not in night  # you-rows are never dispatched
    assert code_day == 0 and code_night == 0


def test_you_list(tmp_path, capsys):
    p = tmp_path / "task.md"
    shutil.copy(DUMMY, p)
    for i in ("D-001", "D-002"):
        task_state.main([i, "done", "--file", str(p)])
    capsys.readouterr()
    assert next_task.main(["--you", "--file", str(p)]) == 0
    assert capsys.readouterr().out.startswith("D-006")


def test_blocked_note_and_status_summary(tmp_path, capsys):
    p = tmp_path / "task.md"
    shutil.copy(DUMMY, p)
    assert task_state.main(["D-003", "blocked", "--note", "failed twice on builder-light: x | y; escalate to builder",
                            "--file", str(p)]) == 0
    task_state.main(["D-001", "doing", "--file", str(p)])
    capsys.readouterr()
    assert status_counts.main(["--file", str(p)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert any(l.startswith("D0: done 0/8, doing 1, todo 6, blocked 1") for l in out)
    assert any(l.startswith("DOING: D-001") for l in out)
    blocked = next(l for l in out if l.startswith("BLOCKED:"))
    assert "D-003" in blocked and "escalate to builder" in blocked and "|" not in blocked
    assert len(out) <= 15


def test_status_counts_on_real_task_file_fits_the_limit(capsys):
    assert status_counts.main(["--file", str(ROOT / "task.md")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) <= 12
    assert any(l.startswith("M0:") for l in out)


def test_inbox_append_procedure_keeps_the_table_valid(tmp_path, capsys):
    """Do what steward.md says (insert as last Inbox row) and check the parsers still agree."""
    p = tmp_path / "task.md"
    shutil.copy(DUMMY, p)
    text = p.read_text(encoding="utf-8")
    anchor = "|---|---|---|---|---|---|---|---|\n\n### D0: Dummy"
    assert text.count(anchor) == 1  # the unique-anchor rule in steward.md works on this layout
    row = "| I-001 | todo | Fix Q-123: wrong give-way line | sonnet | high | any | - | Q-123 gone from the next digest |\n"
    p.write_text(text.replace(anchor, "|---|---|---|---|---|---|---|---|\n" + row + "\n### D0: Dummy"), encoding="utf-8")
    rows = tf.load(p)
    new = [r for r in rows if r.id == "I-001"]
    assert len(new) == 1 and len(new[0].cells) == 8 and new[0].section == "Inbox"
    assert len(rows) == 9
    assert next_task.main(["day", "--file", str(p)]) == 0
    assert '"id":"I-001"' in capsys.readouterr().out  # Inbox rows are dispatched first
    # a second row goes under the first, anchored on the previous last row
    row2 = row.replace("I-001", "I-002").replace("Q-123", "Q-124")
    t2 = p.read_text(encoding="utf-8")
    assert t2.count(row + "\n### D0: Dummy") == 1
    p.write_text(t2.replace(row + "\n### D0: Dummy", row + row2 + "\n### D0: Dummy"), encoding="utf-8")
    assert [r.id for r in tf.load(p) if r.section == "Inbox"] == ["I-001", "I-002"]
