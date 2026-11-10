---
name: triage
description: Morning triage for BeamNG Belfast. Reads only the nightly digest, closes fixed issues, adds new Inbox tasks with model tags, and writes reports/today.md (under 20 lines). Exits cleanly when there is no digest yet.
disable-model-invocation: true
model: claude-haiku-5-5
effort: medium
allowed-tools: Agent Read Glob Grep Bash(uv run *) PowerShell(Test-Path *) PowerShell(Get-Date *)
---

You are the dispatcher for triage. Read ONLY the digest, never raw logs or images. Change no code.

## 1. Find the digest

T-307 will define where `DIGEST.md` is written. Until it does, check these two paths in order and use the first that exists:
1. `H:\BelfastData\reports\latest\DIGEST.md` (chosen first: the nightly run writes its output next to the other build data on H:, outside the public repo)
2. `reports/DIGEST.md` in the repository (fallback: `reports/` is the repo's ignored runtime-output folder)

Check each with PowerShell `Test-Path`. If neither exists, reply exactly `No digest yet (T-307 and T-309 write it). Nothing to triage.` and stop. Write no file.

## 2. Delegate to the steward

Use the Agent tool with `subagent_type` `steward`. Do NOT pass a `model` argument. The prompt is this, with `<path>` filled in:

```text
Job: morning triage. Digest: <path>. Read only that file (it is under 60 lines), then do these in order.
1. Fixed: for each issue ID (like Q-123) that the digest lists as fixed, closed or resolved, open issues/<ID>.md and set its Status line to `closed` (add `Status: closed` under the title if there is none). Touch no other card.
2. New: for each issue or regression the digest lists as new or open, skip it if its ID already appears anywhere in task.md. Otherwise add one Inbox row (follow your Inbox rules): Task `Fix <ID>: <title>`, Done when `<ID> no longer appears in the next digest`. Choose Model/Effort/Where from your tag guide. Add at most 5 rows; list the rest in today.md.
3. Write reports/today.md, under 20 lines: title with today's date; one line of build status from the digest; Fixed (IDs); New (IDs and Inbox IDs); Blocked (the BLOCKED line of `uv run --no-project --python 3.12 python .claude/skills/status/status_counts.py`); a section "Needs you" with the rows from `uv run --no-project --python 3.12 python tools/next_task.py --you` and anything in the digest that only the user can decide. Do not copy the digest.
If the digest has a form you cannot read, write today.md saying so and change nothing else. Reply in your report format.
```

## 3. Finish

Reply in at most 6 lines: the digest path used, counts of closed cards and new Inbox rows, and the steward's NEEDS YOU line. If the steward reports FAIL, say so plainly.
