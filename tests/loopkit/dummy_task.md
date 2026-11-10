# Dummy task file for the loop kit dry run

Same table format as the real task.md. Nothing here is a real task.

## 6. Quality gates

| Gate | Runs | Passes when |
|---|---|---|
| G0 | no game | never matched by the parser |

## 7. Milestones and tasks

Status values are `todo`, `doing`, `done` and `blocked`.

### Inbox

New requests land here. The dispatcher takes them before the milestones.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|

### D0: Dummy

Outcome: every loop-kit command can be walked through on harmless tasks.

| ID | Status | Task | Model | Effort | Where | Needs | Done when |
|---|---|---|---|---|---|---|---|
| D-001 | todo | Create `H:\BelfastData\spikes\loopkit\hello.txt` containing the text `ok` (outside the repo) | haiku | medium | any | - | The file exists and its content is exactly `ok` |
| D-002 | todo | Create `hello_repo.txt` in the repository root containing the text `ok2` and commit it | sonnet | high | any | D-001 | `hello_repo.txt` exists with content `ok2` and is committed on the branch |
| D-003 | todo | Create `H:\BelfastData\spikes\loopkit\fail.txt` containing `ok` (this task is built to fail) | haiku | medium | any | D-002 | `fail.txt` contains `ok` and `fail.txt` does not exist |
| D-004 | todo | Task tagged for a model that has no agent | fable | high | any | D-002 | never |
| D-005 | todo | Game task: write `game-ok` into `H:\BelfastData\spikes\loopkit\game.txt` (no game is launched) | sonnet | high | game | D-002 | The file exists with content `game-ok` |
| D-006 | todo | A task only the user can do | you | - | you | D-002 | The user says so |
| D-007 | todo | Design note task for the architect | opus | medium | any | D-002 | never |
| D-008 | todo | Hard design task | opus | xhigh | any | D-007 | never |

## 8. Guard rails

| x | y |
|---|---|
| T-999 | todo |
