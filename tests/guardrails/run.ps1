# tests/guardrails/run.ps1 - feeds representative tool-call JSON to .claude/hooks/guard.ps1
# and asserts block (exit 2) or allow (exit 0). Prints a pass/fail table; exits 1 on any failure.
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File tests\guardrails\run.ps1
#
# Safe by design: it never touches the real repository index or the Steam folder. Cases that need
# git state (staged secrets, current branch) use throw-away repos under $env:TEMP.
# Secret-looking strings are assembled at run time so this file itself contains no token.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $here '..\..')).Path
$guard = Join-Path $root '.claude\hooks\guard.ps1'
if (-not (Test-Path -LiteralPath $guard)) { Write-Host "guard.ps1 not found: $guard"; exit 1 }

# ---------------------------------------------------------------- fixtures
$S = 'H:\SteamLibrary\steamapps\common\BeamNG.drive'
$SU = '/h/SteamLibrary/steamapps/common/BeamNG.drive'
$mly = 'MLY' + '|' + '1234567890123456' + '|' + ('a1B2c3D4' * 4)
$ghp = 'gh' + 'p_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'
$ghpat = 'github' + '_pat_' + '11ABCDEFG0abcdefghij_KLMNOPQRSTUVWXYZ0123456789'
$ant = 'sk' + '-ant-' + 'api03-' + ('Zz9' * 12)
$pk = '-----BEGIN ' + 'RSA PRIVATE KEY' + '-----'
$mcp = '-mcp' + 'remote'

$tmp = Join-Path $env:TEMP ('guardtest-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmp | Out-Null

function Run-Cmd([string]$line) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'cmd.exe'
    $psi.Arguments = '/c ' + $line + ' >nul 2>&1'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.WaitForExit()
}

function New-Repo([string]$name, [string]$branch) {
    $d = Join-Path $tmp $name
    New-Item -ItemType Directory -Path $d | Out-Null
    Run-Cmd "git -C `"$d`" init -q"
    Run-Cmd "git -C `"$d`" symbolic-ref HEAD refs/heads/$branch"
    Set-Content -LiteralPath (Join-Path $d 'README.md') -Value 'hello' -Encoding ASCII
    Run-Cmd "git -C `"$d`" add README.md"
    Run-Cmd "git -C `"$d`" -c user.name=t -c user.email=t@example.invalid commit -q -m init"
    return $d
}

$repoDev = New-Repo 'dev' 'dev'
$repoMain = New-Repo 'main' 'main'
$repoClean = New-Repo 'clean' 'dev'
Set-Content -LiteralPath (Join-Path $repoClean 'notes.txt') -Value 'just notes' -Encoding ASCII
Run-Cmd "git -C `"$repoClean`" add notes.txt"
$repoStaged = New-Repo 'staged' 'dev'
Set-Content -LiteralPath (Join-Path $repoStaged 'conf.json') -Value ('{"token": "' + $mly + '"}') -Encoding ASCII
Run-Cmd "git -C `"$repoStaged`" add conf.json"
$repoUntracked = New-Repo 'untracked' 'dev'
Set-Content -LiteralPath (Join-Path $repoUntracked 'leak.txt') -Value ('key=' + $ant) -Encoding ASCII
$repoModified = New-Repo 'modified' 'dev'
Add-Content -LiteralPath (Join-Path $repoModified 'README.md') -Value ('token ' + $ghp) -Encoding ASCII
$repoUntrackedClean = New-Repo 'untrackedclean' 'dev'
Set-Content -LiteralPath (Join-Path $repoUntrackedClean 'data.txt') -Value 'nothing secret' -Encoding ASCII

# ---------------------------------------------------------------- case list
$cases = New-Object System.Collections.Generic.List[object]
function C([string]$rule, [string]$desc, [string]$expect, [string]$tool, $input_, [string]$cwd) {
    $cases.Add([pscustomobject]@{ Rule = $rule; Desc = $desc; Expect = $expect; Tool = $tool; Input = $input_; Cwd = $cwd; Raw = $null })
}
function CR([string]$rule, [string]$desc, [string]$expect, [string]$raw) {
    $cases.Add([pscustomobject]@{ Rule = $rule; Desc = $desc; Expect = $expect; Tool = ''; Input = $null; Cwd = ''; Raw = $raw })
}
function Sh([string]$cmd) { return @{ command = $cmd } }

# ---- rule 1: Steam folder (blocked)
C 'R1 steam' 'Write into Steam (backslash)' 'block' 'Write' @{ file_path = "$S\lua\x.lua"; content = 'x' } $root
C 'R1 steam' 'Edit into Steam (lowercase, forward slash)' 'block' 'Edit' @{ file_path = 'h:/steamlibrary/steamapps/common/beamng.drive/lua/x.lua'; old_string = 'a'; new_string = 'b' } $root
C 'R1 steam' 'NotebookEdit in Steam' 'block' 'NotebookEdit' @{ notebook_path = "$S\n.ipynb"; new_source = 'x' } $root
C 'R1 steam' 'Write with Git-Bash form /h/SteamLibrary' 'block' 'Write' @{ file_path = "$SU/x.txt"; content = 'x' } $root
C 'R1 steam' 'Write with .. traversal into Steam' 'block' 'Write' @{ file_path = "H:\Claude\..\SteamLibrary\steamapps\x.txt"; content = 'x' } $root
C 'R1 steam' 'Bash redirect into Steam' 'block' 'Bash' (Sh "echo hi > $SU/x.txt") $root
C 'R1 steam' 'Bash append redirect, backslash path' 'block' 'Bash' (Sh "echo hi >> `"$S\x.txt`"") $root
C 'R1 steam' 'Bash rm -rf in Steam' 'block' 'Bash' (Sh "rm -rf `"$S\mods`"") $root
C 'R1 steam' 'Bash cp INTO Steam' 'block' 'Bash' (Sh "cp foo.lua $SU/lua/") $root
C 'R1 steam' 'Bash mv out of Steam' 'block' 'Bash' (Sh "mv $SU/x /tmp/") $root
C 'R1 steam' 'Bash cd Steam && touch' 'block' 'Bash' (Sh "cd $SU && touch foo") $root
C 'R1 steam' 'Bash bash -c wrapper rm' 'block' 'Bash' (Sh "bash -c `"rm $SU/x`"") $root
C 'R1 steam' 'Bash sed -i in Steam' 'block' 'Bash' (Sh "sed -i s/a/b/ $SU/x.lua") $root
C 'R1 steam' 'Bash tee into Steam' 'block' 'Bash' (Sh "echo hi | tee $SU/x.txt") $root
C 'R1 steam' 'Bash find -delete in Steam' 'block' 'Bash' (Sh "find $SU -name '*.bak' -delete") $root
C 'R1 steam' 'PowerShell Remove-Item (mixed case)' 'block' 'PowerShell' (Sh 'Remove-Item -Recurse -Force H:\steamlibrary\steamapps\common\beamng.drive\x') $root
C 'R1 steam' 'PowerShell Set-Content' 'block' 'PowerShell' (Sh "Set-Content -Path '$S\a.txt' -Value 1") $root
C 'R1 steam' 'PowerShell Copy-Item INTO Steam' 'block' 'PowerShell' (Sh "Copy-Item C:\tmp\a.lua $S\lua\") $root
C 'R1 steam' 'PowerShell Out-File via pipe' 'block' 'PowerShell' (Sh "'x' | Out-File '$S\o.txt'") $root
C 'R1 steam' 'PowerShell gci | Remove-Item' 'block' 'PowerShell' (Sh "Get-ChildItem $S\x | Remove-Item") $root
C 'R1 steam' 'PowerShell .NET WriteAllText' 'block' 'PowerShell' (Sh "[IO.File]::WriteAllText('$S\x.txt','a')") $root
C 'R1 steam' 'PowerShell nested powershell -Command' 'block' 'PowerShell' (Sh "powershell -NoProfile -Command `"Remove-Item $S\x`"") $root
C 'R1 steam' 'Bash robocopy INTO Steam' 'block' 'Bash' (Sh "robocopy C:\tmp $S /E") $root
# ---- rule 1: allowed look-alikes
C 'R1 steam' 'Bash cat from Steam piped to head' 'allow' 'Bash' (Sh "cat $SU/lua/ge/main.lua | head -20") $root
C 'R1 steam' 'PowerShell Get-Content from Steam' 'allow' 'PowerShell' (Sh "Get-Content '$S\lua\ge\main.lua' -TotalCount 5") $root
C 'R1 steam' 'Bash ls / rg in Steam' 'allow' 'Bash' (Sh "ls `"$S`" && rg -n function $SU/lua") $root
C 'R1 steam' 'Bash cp FROM Steam to repo' 'allow' 'Bash' (Sh "cp $SU/lua/x.lua /h/Claude/BeamNGBelfast/data/ref/") $root
C 'R1 steam' 'PowerShell Copy-Item FROM Steam' 'allow' 'PowerShell' (Sh "Copy-Item '$S\lua\x.lua' -Destination H:\Claude\BeamNGBelfast\ref\") $root
C 'R1 steam' 'PowerShell Get-Content Steam | Set-Content repo' 'allow' 'PowerShell' (Sh "Get-Content '$S\x.lua' | Set-Content H:\Claude\out.txt") $root
C 'R1 steam' 'Bash robocopy FROM Steam' 'allow' 'Bash' (Sh "robocopy $S\lua C:\work\lua /E") $root
C 'R1 steam' 'Bash git -C Steam status (read-only)' 'allow' 'Bash' (Sh "git -C $SU status") $root
C 'R1 steam' 'Write a doc that mentions the Steam path' 'allow' 'Write' @{ file_path = "$root\docs\steam-notes.md"; content = "Never write to $S." } $root
C 'R1 steam' 'Bash redirect to repo, text mentions Steam' 'allow' 'Bash' (Sh "echo `"do not write to $SU`" > docs/note.md") $root
C 'R1 steam' 'Write to look-alike H:\SteamLibraryBackup' 'allow' 'Write' @{ file_path = 'H:\SteamLibraryBackup\x.txt'; content = 'x' } $root
C 'R1 steam' 'Bash rm in repo scratch dir' 'allow' 'Bash' (Sh 'rm -rf /h/Claude/BeamNGBelfast/build/tmp') $root

# ---- rule 2: pushes to main (blocked)
C 'R2 push' 'git push origin main' 'block' 'Bash' (Sh 'git push origin main') $root
C 'R2 push' 'git push origin HEAD:main' 'block' 'Bash' (Sh 'git push origin HEAD:main') $root
C 'R2 push' 'git push origin dev:main' 'block' 'Bash' (Sh 'git push origin dev:main') $root
C 'R2 push' 'git push origin +main' 'block' 'Bash' (Sh 'git push origin +main') $root
C 'R2 push' 'git push --force origin dev' 'block' 'Bash' (Sh 'git push --force origin dev') $root
C 'R2 push' 'git push -f origin dev' 'block' 'Bash' (Sh 'git push -f origin dev') $root
C 'R2 push' 'git push --force-with-lease' 'block' 'Bash' (Sh 'git push --force-with-lease origin dev') $root
C 'R2 push' 'git push --all origin' 'block' 'Bash' (Sh 'git push --all origin') $root
C 'R2 push' 'git push --mirror' 'block' 'Bash' (Sh 'git push --mirror origin') $root
C 'R2 push' 'full ref refs/heads/dev:refs/heads/main' 'block' 'Bash' (Sh 'git push origin refs/heads/dev:refs/heads/main') $root
C 'R2 push' 'git -C . push -u origin main (chained)' 'block' 'Bash' (Sh 'cd /tmp && git -C . push -u origin main') $root
C 'R2 push' 'bare git push while on main' 'block' 'Bash' (Sh 'git push') $repoMain
C 'R2 push' 'git push origin HEAD while on main' 'block' 'Bash' (Sh 'git push origin HEAD') $repoMain
C 'R2 push' 'PowerShell git push origin main' 'block' 'PowerShell' (Sh 'git push origin main') $root
C 'R2 push' 'gh pr merge' 'block' 'Bash' (Sh 'gh pr merge 12 --squash') $root
# ---- rule 2: allowed look-alikes
C 'R2 push' 'git push origin dev' 'allow' 'Bash' (Sh 'git push origin dev') $root
C 'R2 push' 'git push -u origin dev' 'allow' 'Bash' (Sh 'git push -u origin dev') $root
C 'R2 push' 'git push origin dev:dev' 'allow' 'Bash' (Sh 'git push origin dev:dev') $root
C 'R2 push' 'git push origin main:dev (main is only the source)' 'allow' 'Bash' (Sh 'git push origin main:dev') $root
C 'R2 push' 'bare git push while on dev' 'allow' 'Bash' (Sh 'git push') $repoDev
C 'R2 push' 'git status' 'allow' 'Bash' (Sh 'git status') $root
C 'R2 push' 'git log main..dev' 'allow' 'Bash' (Sh 'git log --oneline main..dev') $root
C 'R2 push' 'git pull origin main (pull is not push)' 'allow' 'Bash' (Sh 'git pull origin main') $root
C 'R2 push' 'commit message that quotes the command' 'allow' 'Bash' (Sh 'git commit --allow-empty -m "docs: never run git push origin main"') $repoClean
C 'R2 push' 'echo of the command' 'allow' 'Bash' (Sh 'echo "git push origin main is forbidden"') $root
C 'R2 push' 'Write a doc that mentions main' 'allow' 'Write' @{ file_path = "$root\docs\branching.md"; content = "Never push to main. Use git push origin dev. The main branch is protected." } $root

# ---- rule 3: secrets (blocked)
C 'R3 secrets' 'Write file containing a Mapillary token' 'block' 'Write' @{ file_path = "$root\config\mapillary.json"; content = "{`"token`": `"$mly`"}" } $root
C 'R3 secrets' 'Edit adds a GitHub PAT' 'block' 'Edit' @{ file_path = "$root\README.md"; old_string = 'a'; new_string = "token $ghpat" } $root
C 'R3 secrets' 'Write file containing a ghp token' 'block' 'Write' @{ file_path = "$root\tools\x.py"; content = "T = '$ghp'" } $root
C 'R3 secrets' 'Write file containing an Anthropic key' 'block' 'Write' @{ file_path = "$root\config\x.json"; content = "key $ant" } $root
C 'R3 secrets' 'Write file containing a private key block' 'block' 'Write' @{ file_path = "$root\config\id.txt"; content = "$pk`nabc" } $root
C 'R3 secrets' 'git add .env' 'block' 'Bash' (Sh 'git add .env') $repoDev
C 'R3 secrets' 'git add -f secrets.env' 'block' 'Bash' (Sh 'git add -f config/secrets.env') $repoDev
C 'R3 secrets' 'git add *.pem' 'block' 'Bash' (Sh 'git add keys/server.pem') $repoDev
C 'R3 secrets' 'PowerShell git add x.key' 'block' 'PowerShell' (Sh 'git add certs\x.key') $repoDev
C 'R3 secrets' 'git commit with staged Mapillary token' 'block' 'Bash' (Sh 'git commit -m "add conf"') $repoStaged
C 'R3 secrets' 'git add . with untracked file holding an Anthropic key' 'block' 'Bash' (Sh 'git add .') $repoUntracked
C 'R3 secrets' 'git commit -am with modified tracked file holding a ghp token' 'block' 'Bash' (Sh 'git commit -am "update readme"') $repoModified
# ---- rule 3: allowed look-alikes
C 'R3 secrets' 'Write ordinary doc' 'allow' 'Write' @{ file_path = "$root\docs\notes.md"; content = 'Mapillary tokens look like MLY|<id>|<secret>; keep them out of git.' } $root
C 'R3 secrets' 'Write doc describing a PEM header with ellipsis' 'allow' 'Write' @{ file_path = "$root\docs\secrets.md"; content = 'Block private key headers such as -----BEGIN ... PRIVATE KEY-----.' } $root
C 'R3 secrets' 'Write token-like content OUTSIDE the repo' 'allow' 'Write' @{ file_path = (Join-Path $tmp 'outside.txt'); content = "token $ghp" } $root
C 'R3 secrets' 'git add .env.example' 'allow' 'Bash' (Sh 'git add .env.example') $repoDev
C 'R3 secrets' 'git add README.md' 'allow' 'Bash' (Sh 'git add README.md') $repoDev
C 'R3 secrets' 'git add . with clean untracked file' 'allow' 'Bash' (Sh 'git add .') $repoUntrackedClean
C 'R3 secrets' 'git commit with clean staged file' 'allow' 'Bash' (Sh 'git commit -m "add notes"') $repoClean
C 'R3 secrets' 'git diff / status are untouched' 'allow' 'Bash' (Sh 'git diff --cached; git status') $repoStaged

# ---- rule 4: -mcpremote (blocked)
C 'R4 mcpremote' 'Bash launch with the flag' 'block' 'Bash' (Sh "/h/SteamLibrary/steamapps/common/BeamNG.drive/Bin64/BeamNG.drive.x64.exe -userpath X $mcp") $root
C 'R4 mcpremote' 'PowerShell Start-Process with the flag' 'block' 'PowerShell' (Sh "Start-Process BeamNG.drive.x64.exe -ArgumentList '-userpath','x','$mcp'") $root
C 'R4 mcpremote' 'python gamectl with the flag' 'block' 'Bash' (Sh "python tools/gamectl.py launch --extra `"$mcp`"") $root
C 'R4 mcpremote' 'bash -c wrapper with the flag' 'block' 'Bash' (Sh "bash -c `"game.exe $mcp`"") $root
C 'R4 mcpremote' 'Write launcher .bat with the flag' 'block' 'Write' @{ file_path = "$root\tools\launch.bat"; content = "BeamNG.drive.x64.exe $mcp" } $root
C 'R4 mcpremote' 'Write Python with the flag' 'block' 'Write' @{ file_path = "$root\tools\gamectl.py"; content = "ARGS = ['-userpath', 'x', '$mcp']" } $root
C 'R4 mcpremote' 'Edit json adds the flag' 'block' 'Edit' @{ file_path = "$root\config\game.json"; old_string = '[]'; new_string = "[`"$mcp`"]" } $root
C 'R4 mcpremote' 'heredoc writes the flag into a .ps1' 'block' 'Bash' (Sh "cat > tools/run.ps1 <<'EOF'`ngame.exe $mcp`nEOF") $root
# ---- rule 4: allowed look-alikes
C 'R4 mcpremote' 'Write markdown doc that names the flag' 'allow' 'Write' @{ file_path = "$root\docs\rules.md"; content = "Never pass $mcp." } $root
C 'R4 mcpremote' 'Write a test under tests/guardrails/' 'allow' 'Write' @{ file_path = "$root\tests\guardrails\extra.ps1"; content = "`$x = '$mcp'" } $root
C 'R4 mcpremote' 'Write a hook under .claude/hooks/' 'allow' 'Write' @{ file_path = "$root\.claude\hooks\other.ps1"; content = "# blocks $mcp" } $root
C 'R4 mcpremote' 'Write code using the bare word (no dash)' 'allow' 'Write' @{ file_path = "$root\tools\check.py"; content = "assert 'mcpremote' not in args" } $root
C 'R4 mcpremote' 'rg searching for the word' 'allow' 'Bash' (Sh 'rg -n mcpremote docs') $root
C 'R4 mcpremote' 'rg searching for the flag after --' 'allow' 'Bash' (Sh "rg -n -- $mcp docs") $root
C 'R4 mcpremote' 'git commit message that names the flag' 'allow' 'Bash' (Sh "git commit --allow-empty -m `"never pass $mcp`"") $repoClean
C 'R4 mcpremote' 'heredoc commit message that names the flag' 'allow' 'Bash' (Sh "git commit --allow-empty -F - <<'EOF'`nnever pass $mcp`nEOF") $repoClean
C 'R4 mcpremote' 'launch with -enablemcp only' 'allow' 'Bash' (Sh 'BeamNG.drive.x64.exe -userpath H:\BeamNG-QA\profile -enablemcp') $root

# ---- robustness: fail-open and unrelated calls
CR 'generic' 'empty stdin (fail-open)' 'allow' ''
CR 'generic' 'malformed JSON (fail-open)' 'allow' 'this is { not json'
CR 'generic' 'JSON without tool_input (fail-open)' 'allow' '{"tool_name":"Bash"}'
C 'generic' 'ls' 'allow' 'Bash' (Sh 'ls -la') $root
C 'generic' 'Read tool on a Steam file (reading is fine)' 'allow' 'Read' @{ file_path = "$S\lua\x.lua" } $root
C 'generic' 'Write an ordinary source file' 'allow' 'Write' @{ file_path = "$root\tools\ok.py"; content = "print('hello')" } $root
C 'generic' 'PowerShell Get-ChildItem' 'allow' 'PowerShell' (Sh 'Get-ChildItem -Recurse docs | Select-Object -First 3') $root

# ---------------------------------------------------------------- runner
function Invoke-Guard($case) {
    if ($null -ne $case.Raw) { $payload = $case.Raw }
    else {
        $obj = [ordered]@{ session_id = 't'; cwd = $case.Cwd; hook_event_name = 'PreToolUse'; tool_name = $case.Tool; tool_input = $case.Input; tool_use_id = 'toolu_test' }
        $payload = $obj | ConvertTo-Json -Depth 6 -Compress
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $psi.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $guard + '"'
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables['CLAUDE_PROJECT_DIR'] = $root
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $p = [System.Diagnostics.Process]::Start($psi)
    $errTask = $p.StandardError.ReadToEndAsync()
    $outTask = $p.StandardOutput.ReadToEndAsync()
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($payload)
    $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $p.StandardInput.Close()
    [void]$p.WaitForExit(30000)
    $sw.Stop()
    return [pscustomobject]@{ Code = $p.ExitCode; Err = $errTask.Result.Trim(); Ms = [int]$sw.ElapsedMilliseconds }
}

$results = New-Object System.Collections.Generic.List[object]
$n = 0
foreach ($case in $cases) {
    $n++
    $r = Invoke-Guard $case
    $got = 'other'
    if ($r.Code -eq 2) { $got = 'block' } elseif ($r.Code -eq 0) { $got = 'allow' }
    $ok = ($got -eq $case.Expect)
    $results.Add([pscustomobject]@{ N = $n; Rule = $case.Rule; Expect = $case.Expect; Got = $got; Status = $(if ($ok) { 'PASS' } else { 'FAIL' }); Ms = $r.Ms; Desc = $case.Desc; Err = $r.Err })
}

Write-Host ''
$results | ForEach-Object {
    $line = '{0,3} {1,-4} {2,-12} {3,-6} {4,-6} {5,5}ms  {6}' -f $_.N, $_.Status, $_.Rule, $_.Expect, $_.Got, $_.Ms, $_.Desc
    Write-Host $line
    if ($_.Status -eq 'FAIL') { Write-Host ('        stderr: ' + $_.Err) }
}

Write-Host ''
Write-Host 'Per-rule coverage (blocked / allowed cases):'
$rules = @('R1 steam', 'R2 push', 'R3 secrets', 'R4 mcpremote')
$coverageOk = $true
foreach ($rl in $rules) {
    $b = @($results | Where-Object { $_.Rule -eq $rl -and $_.Expect -eq 'block' }).Count
    $a = @($results | Where-Object { $_.Rule -eq $rl -and $_.Expect -eq 'allow' }).Count
    $okc = ($b -ge 2 -and $a -ge 2)
    if (-not $okc) { $coverageOk = $false }
    Write-Host ('  {0,-14} blocked={1,-3} allowed={2,-3} {3}' -f $rl, $b, $a, $(if ($okc) { 'ok' } else { 'TOO FEW CASES' }))
}

$fail = @($results | Where-Object { $_.Status -eq 'FAIL' }).Count
$pass = $results.Count - $fail
$maxMs = ($results | Measure-Object -Property Ms -Maximum).Maximum
$avgMs = [int](($results | Measure-Object -Property Ms -Average).Average)
Write-Host ''
Write-Host ("Total {0}: {1} passed, {2} failed. Hook latency avg {3} ms, max {4} ms (includes powershell.exe startup)." -f $results.Count, $pass, $fail, $avgMs, $maxMs)

# cleanup of the throw-away repos (only ever under $env:TEMP)
try { if ($tmp.StartsWith($env:TEMP) -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Recurse -Force } } catch { }

if ($fail -gt 0 -or -not $coverageOk) { Write-Host 'RESULT: FAIL'; exit 1 }
Write-Host 'RESULT: PASS'
exit 0
