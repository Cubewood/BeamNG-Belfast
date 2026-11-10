---
name: next-task
description: Run the next ready task from the BeamNG Belfast queue on the model its tag asks for. One task per run. Argument is day or night.
argument-hint: <day|night>
disable-model-invocation: true
model: claude-haiku-5-5
effort: medium
allowed-tools: Agent Read Glob Grep Bash(uv run *) Bash(git *) PowerShell(uv run *) PowerShell(git *) PowerShell(Get-Process *)
---

You are the dispatcher. Run ONE task, then stop. Follow the steps in order. The mode is: $ARGUMENTS

If the mode is not exactly `day` or `night`, reply `usage: /next-task day|night` and stop. Never push `main`. Never launch the game yourself.

## 1. Find the task

Run `uv run --no-project --python 3.12 python tools/next_task.py <mode>` from the repository root.
- Exit code 3 and no output: reply `No ready task.` and stop.
- Any other non-zero exit code: reply with the error line and stop.
- Exit code 0: stdout is one JSON object (id, status, task, model, effort, where, needs, done_when). Call it ROW.

## 2. Night guards (mode `night` only; `day` rows are always Where=any)

Run this in PowerShell: `$b=[bool](Get-Process -Name 'BeamNG*' -ErrorAction SilentlyContinue); $h=(Get-Date).Hour; "BEAMNG_RUNNING=$b HOUR=$h"`
- `BEAMNG_RUNNING=True`: reply `BeamNG is running; no night run.` and stop. Change nothing.
- ROW.where is `game` and HOUR is 9 to 17: reply `Game tasks do not run 09:00-18:00.` and stop. Change nothing.

## 3. Pick the agent

| ROW.model | ROW.effort | agent |
|---|---|---|
| sonnet | medium or high | builder |
| haiku | any | builder-light |
| opus | medium | architect |
| opus | high | architect-high |
| opus | xhigh | architect-deep |

Any other model or effort (fable, local, script, ...): run `uv run --no-project --python 3.12 python tools/task_state.py <id> blocked --note "no agent for <model>/<effort>; needs the user"`, reply with that one line and stop.

## 4. Start

1. `git branch --show-current`. If it is not `dev`, run `git switch dev`. If that fails, reply with the error and stop.
2. `uv run --no-project --python 3.12 python tools/task_state.py <id> doing`
3. `git commit -m "<id>: doing" -- task.md`
4. `git switch -c loop/<id>` (if the branch already exists: `git switch loop/<id>`).

## 5. Delegate (attempt 1 of 2)

Use the Agent tool with `subagent_type` set to the agent from step 3. Do NOT pass a `model` argument: it would override the model the agent is pinned to. The prompt is ONLY this, filled in (no other plan text):

```text
Task brief from /next-task <mode>. You are on branch loop/<id>. Commit there; do not merge or push.
Row: <ROW as JSON>
Done when (run this check yourself): <ROW.done_when>
Pointers: CLAUDE.md (read first); your row is in task.md (find it by ID only); other files named in the row.
Game: GAME ALLOWED (only if mode is night and ROW.where is game) otherwise: no game.
Local model: MODEL ALLOWED (only if mode is night and ROW.where is gpu) otherwise: no local model.
Reply in the report format from your instructions.
```

## 6. Judge the report

PASS only if all of these hold, otherwise it is a FAIL:
- The first line is `RESULT: PASS` and EVIDENCE shows the Done-when check and its output.
- If EVIDENCE names a command that needs no game and no local model, run it once yourself. It must agree.
- `git status --porcelain --untracked-files=no` prints nothing (no uncommitted tracked changes).

On FAIL: repeat step 5 once (attempt 2 of 2) with the same agent and add the line `Previous attempt failed: <first 10 lines of its report>`. If attempt 2 also fails, go to step 8.

## 7. On PASS

1. `git switch dev`, then `git merge loop/<id>` (plain merge, no flags). If it stops with a conflict: `git merge --abort` and go to step 8 with the reason `merge conflict`.
2. `uv run --no-project --python 3.12 python tools/task_state.py <id> done`
3. `git commit -m "<id>: done" -- task.md`
4. `git branch --show-current` must print `dev`. Then `git push origin dev`. Never push any other branch.
5. Reply in at most 5 lines: `<id> done`, the agent used, EVIDENCE in one line, whether the push worked.

## 8. On two FAILs

1. If tracked files are modified: `git stash push -m "<id> failed"`.
2. `git switch dev`.
3. `uv run --no-project --python 3.12 python tools/task_state.py <id> blocked --note "failed twice on <agent>: <reason in under 15 words>; escalate to <next>"` where `<next>` is: builder-light -> builder; builder -> architect; architect -> architect-high; architect-high -> architect-deep; architect-deep -> the user (Fable only after the user agrees). Do not use the `|` character in the note.
4. `git commit -m "<id>: blocked" -- task.md`, then `git push origin dev` (check you are on `dev` first).
5. Reply in at most 5 lines: `<id> blocked`, the reason, the branch `loop/<id>` kept. Stop. Do not start another task.
