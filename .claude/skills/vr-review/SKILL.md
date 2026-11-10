---
name: vr-review
description: Turn VR flag records and notes in feedback/ into issue cards in issues/ and Inbox tasks. Says so and stops when there are no flags and no new notes.
disable-model-invocation: true
model: claude-haiku-5-5
effort: medium
allowed-tools: Agent Read Glob Grep Bash(uv run *)
---

You are the dispatcher for the VR review. Change no code.

## 1. Look for input

- Notes: `Glob` for `feedback/*.md`. A note is NEW if its path does not appear in the `Source:` line of any file in `issues/` (use `Grep` on `issues/`). Ignore `.gitkeep` and files named README.
- Flag records: the flag-this-spot key is built in T-308, which will say where it stores records. Until T-308 is `done` in `task.md` and you can name that location, there are none.

If there are no NEW notes and no flag records, reply exactly `Nothing to review: no flag records and no new notes in feedback/.` and stop. Write no file.

## 2. Delegate to the steward

Use the Agent tool with `subagent_type` `steward`. Do NOT pass a `model` argument. The prompt is this, with the file list filled in:

```text
Job: VR review. New notes: <paths>. Flag records: <paths or none>. Read only those files.
A note follows this template: Date, Build, Route or area, Comfort 1 to 5, Smooth frame rate yes or no, then "Worst three problems (where, what you expected, what you saw)".
For each problem in a note (or each flag record) do both of these:
1. Write issues/V-<note date as yyyymmdd>-<nn>.md (nn = 01, 02, ... for that date; never overwrite a file that exists) with exactly these lines: `# V-<yyyymmdd>-<nn>: <short title>`, `Status: open`, `Source: <note or flag path>`, `Where: <where>`, `Expected: <what was expected>`, `Saw: <what was seen>`, `Build: <build>`, `Task: <Inbox ID>`.
2. Add one Inbox row for it (follow your Inbox rules): Task `Fix V-<yyyymmdd>-<nn>: <title>`, Done when `the user no longer flags it, or a check proves it fixed`. Choose Model/Effort/Where from your tag guide.
If a note has no "Worst three problems" part, skip it and say which file in your report. Do not edit the note files. Add at most 6 cards per run. Reply in your report format and list the card IDs and Inbox IDs.
```

## 3. Finish

Reply in at most 6 lines: notes and flags processed, card IDs, Inbox IDs, and the steward's NEEDS YOU line. If the steward reports FAIL, say so plainly.
