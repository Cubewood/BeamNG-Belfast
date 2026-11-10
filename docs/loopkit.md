# Loop kit (T-010)

How a cheap dispatcher runs one task per session on the right model. Files:

- `.claude/agents/`: `builder`, `builder-light`, `architect`, `architect-high`, `architect-deep`, `steward`
- `.claude/skills/<name>/SKILL.md`: `/next-task`, `/status`, `/triage`, `/qa`, `/truth`, `/vr-review`
- `.claude/skills/status/status_counts.py`: read-only counts helper for `/status`
- `tests/loopkit/`: static tests, a dummy task file and the dry-run setup

## File formats (checked against the Claude Code docs, v2.1.321)

Sources: code.claude.com/docs/en/sub-agents, /skills, /model-config, /commands.

- Subagent: `.claude/agents/<name>.md`, YAML frontmatter then the system prompt. Required `name`, `description`. Optional `tools`, `model`, `effort`, `color`, and others. `model` takes `sonnet`, `opus`, `haiku`, `fable`, a full ID such as `claude-opus-5-5`, or `inherit`. **`effort` is a documented frontmatter key** with values `low`, `medium`, `high`, `xhigh`, `max` ("set `effort` in a skill or subagent markdown file to override the effort level when that skill or subagent runs"). Haiku 5.5, Sonnet 5.5 and Opus 5.5 all take all five levels. Unknown keys are ignored silently, and a file whose YAML does not parse is skipped (this bit us once: `): ` in a description breaks plain YAML).
- Commands: `.claude/commands/*.md` still works, but the docs say "prefer a skill for new work". A command file takes the same fields as a skill. We use skills (`.claude/skills/<name>/SKILL.md`). Frontmatter used: `name`, `description`, `argument-hint`, `disable-model-invocation`, `model`, `effort`, `allowed-tools`. `$ARGUMENTS` holds everything typed after the command. `model` and `effort` apply "for the rest of the current turn" only.
- Why skills and not commands: `/status` is a built-in command (version, model, account). The docs say a project **skill** replaces a built-in command of the same name in a local terminal session. A file in `.claude/commands/` is not documented to do that.

### How model and effort get pinned, and what can override it

| Thing | Resolution order (first that applies) |
|---|---|
| Subagent model | the Agent call's `model` argument (aliases only), then the agent's frontmatter `model`, then `CLAUDE_CODE_SUBAGENT_MODEL`, then the session model |
| Subagent effort | `CLAUDE_CODE_EFFORT_LEVEL` env var, then the agent's frontmatter `effort`, then the session's level |

So the dispatcher must never pass a `model` argument when it calls an agent (the `/next-task` text says so), and the scheduled tasks must not set `CLAUDE_CODE_EFFORT_LEVEL`. There is no per-call effort argument, which is why the Opus ladder has three agents (one per effort level).

## Which agent for which task tag

| Task tag (Model / Effort) | Agent | Runs on | Effort it runs at |
|---|---|---|---|
| sonnet / medium | `builder` | Sonnet 5.5 | high (the sonnet default in task.md 5.1) |
| sonnet / high | `builder` | Sonnet 5.5 | high |
| haiku / any | `builder-light` | Haiku 5.5 | medium |
| opus / medium | `architect` | Opus 5.5 | medium |
| opus / high | `architect-high` | Opus 5.5 | high |
| opus / xhigh | `architect-deep` | Opus 5.5 | xhigh |
| any housekeeping (queue, triage, cards, summaries) | `steward` | Haiku 5.5 | medium |
| fable, local, script, anything else | none | | `/next-task` sets `blocked` with a note |
| you | none | | never dispatched; listed by `next_task.py --you` |

`architect-high` is an addition to the five agents in T-010. The tags in task.md use `opus/high` (T-106, T-701, T-705, T-803), and without it those tasks would run at medium or xhigh. Delete the file and the `opus | high` row in `/next-task` if you would rather not have it.

## Escalation (task.md 5.1) in this kit

5.1: after two failed Done-when checks, raise the effort first, then move up one tier, and note why in the task. Fable only after Opus at xhigh has failed.

`/next-task` tries a task twice with the same agent, then sets `blocked` and writes the next rung in the note (`failed twice on <agent>: <reason>; escalate to <next>`):

| Failed on | Next rung | Note |
|---|---|---|
| `builder-light` (haiku/medium) | `builder` (sonnet/high) | Skips haiku/high: no such agent, Sonnet is the cheaper way up |
| `builder` (sonnet/high) | `architect` (opus/medium) | Sonnet is already at its default high; the next step is a tier |
| `architect` (opus/medium) | `architect-high` (opus/high) | Effort first |
| `architect-high` (opus/high) | `architect-deep` (opus/xhigh) | Effort again |
| `architect-deep` (opus/xhigh) | the user | Fable 5.1 only after the user agrees; there is no Fable agent |

The retag itself (Model and Effort cells, status back to `todo`) is **not automatic**: `tools/task_state.py` only sets Status. The weekly review, `/triage` output (Blocked line) or the user does it by hand. A `--model/--effort` option on `task_state.py` would close this gap.

## Commands

| Command | Does | Runs on | Unlocked by |
|---|---|---|---|
| `/next-task <day\|night>` | Next ready task, one per run, start to merge | Haiku 5.5 (pinned) | T-009 (done) |
| `/status` | At most 15 lines: counts per milestone, doing, blocked, next ready, needs you | session model (not pinned, so the Opus weekly review is not downgraded) | T-009 |
| `/triage` | Reads only the digest, closes fixed cards, adds Inbox rows, writes `reports/today.md` | Haiku 5.5 + `steward` | needs the digest (T-307, T-309) |
| `/qa` | Starts `qa/nightly.ps1` once, after the BeamNG-running and 09:00-18:00 checks | Haiku 5.5 | T-309 |
| `/truth` | Reports it is not available | Haiku 5.5 | T-503, T-506 |
| `/vr-review` | `feedback/*.md` notes (and later flag records) into `issues/V-*.md` cards and Inbox rows | Haiku 5.5 + `steward` | notes work now; flag records need T-308 |

Choices that were not given and that T-307/T-308 may change:

- Digest path: `/triage` checks `H:\BelfastData\reports\latest\DIGEST.md` first, then `reports/DIGEST.md`. The first is chosen because nightly output (frames, metrics) lives under `H:\BelfastData`, outside the public repo. The second is the repo's runtime-output folder (git-ignored). `reports/today.md` stays in the repo because GUIDE.md says so. T-307 should pick one and the other check can go.
- Inbox IDs are `I-001`, `I-002`, ... (highest existing plus one). Issue cards from VR notes are `issues/V-<yyyymmdd>-<nn>.md`; QA cards stay `Q-*` (T-307).
- A VR note counts as handled when its path appears in a `Source:` line under `issues/`. The note itself is never edited.
- Adding an Inbox row is a careful hand edit described in `steward.md` (8 columns, insert as last Inbox row, then check with `next_task.py`). An `add_task` tool in `tools/` would be safer.

## Branch and merge flow of `/next-task`

`task_state doing` and a local commit on `dev`, then `loop/T-xxx` from `dev`, the agent commits there, plain `git merge loop/T-xxx` on pass, `task_state done`, commit `task.md`, `git push origin dev`. Never `main`. On two fails: `blocked` with the note, commit and push `dev`, branch kept. Night mode first checks for a running `BeamNG*` process and, for `game` rows, the 09:00-18:00 window.

## Running it

Scheduled runs use Auto permission mode (GUIDE.md section 5). The T-004 deny rules and hook still apply. The commands list `allowed-tools` for the shell commands they use. If a run hangs, it is probably waiting on a permission prompt.

## Dry run

`tests/loopkit/dryrun_setup.py` builds a throwaway repo and a bare remote under `H:\BelfastData\spikes\loopkit\` from the real kit files and `tests/loopkit/dummy_task.md`. Nothing touches the real `task.md` or this repo's branches. See the T-010 report for what was run.

Static tests: `uv run --no-project --python 3.12 --with pytest --with pyyaml pytest tests/loopkit -q` (with `UV_CACHE_DIR=H:\BelfastData\cache\uv`, `UV_PYTHON_INSTALL_DIR=H:\BelfastData\uv-python`).
