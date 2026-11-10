---
name: qa
description: Start the nightly QA run (qa/nightly.ps1, built in T-309) by hand. Refuses while BeamNG is running or between 09:00 and 18:00.
disable-model-invocation: true
model: claude-haiku-5-5
effort: low
allowed-tools: PowerShell(Test-Path *) PowerShell(Get-Process *) PowerShell(Get-Date *) PowerShell(powershell.exe *)
---

Start the nightly QA run by hand. Follow the steps in order and stop at the first one that says stop.

1. Run in PowerShell: `Test-Path qa/nightly.ps1`. If it prints `False`, reply exactly `QA is not available yet: qa/nightly.ps1 is built in T-309.` and stop.
2. Run in PowerShell: `$b=[bool](Get-Process -Name 'BeamNG*' -ErrorAction SilentlyContinue); $h=(Get-Date).Hour; "BEAMNG_RUNNING=$b HOUR=$h"`
   - `BEAMNG_RUNNING=True`: reply `BeamNG is running; no QA run.` and stop.
   - HOUR is 9 to 17: reply `QA does not run 09:00-18:00.` and stop.
3. Start it once, in the background, without arguments: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File qa/nightly.ps1`. It can run for hours. Never start it a second time.
4. Reply in at most 3 lines: that it started, and that the digest appears after it finishes (T-307 defines where). Do not wait for it. Do not edit anything. Never pass `-mcpremote` to anything.
