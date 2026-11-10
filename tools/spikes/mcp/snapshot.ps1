param([Parameter(Mandatory)][string]$Out)
# Snapshot (path, size, mtime) of the user's normal BeamNG profile locations. Read-only.
$roots = @("$env:LOCALAPPDATA\BeamNG", "$env:LOCALAPPDATA\BeamNG.drive")
Get-ChildItem $roots -Recurse -Force -File -ErrorAction SilentlyContinue |
  Sort-Object FullName |
  ForEach-Object { '{0}|{1}|{2}' -f $_.FullName, $_.Length, $_.LastWriteTimeUtc.ToString('o') } |
  Set-Content -Encoding utf8 $Out
(Get-Content $Out).Count
