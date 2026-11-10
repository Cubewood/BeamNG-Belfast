# guard.ps1 - PreToolUse guard rails for the BeamNG Belfast project (task T-004).
#
# Runs for Bash, PowerShell, Write, Edit, NotebookEdit (see .claude/settings.json).
# Input : hook JSON on stdin (tool_name, tool_input, cwd, ...).
# Output: exit 0 = allow (no output); exit 2 + reason on stderr = block.
# Target: Windows PowerShell 5.1. No &&, no ternary, no ??.
#
# Rules enforced (numbers refer to task.md section 8):
#   1. steam-write : nothing may be written, moved into, copied into, deleted or
#                    redirected into H:\SteamLibrary (any case, / or \, /h/ Git-Bash
#                    form). Reading, and copying FROM that folder, stay allowed.
#   4. no-mcpremote: the -mcpremote game flag must not appear as a shell argument,
#                    nor in written files, except markdown docs, tests/guardrails/
#                    and .claude/hooks/ (they only describe the rule).
#                    The bare word "mcpremote" without a leading dash is always fine.
#   5. no-push-main: git push to main, force pushes, --all, --mirror, a bare
#                    "git push" while main is checked out, "gh pr merge".
#   6. no-secrets  : git add / git commit of secret-named files or token-looking
#                    content; Write/Edit of token-looking content inside the repo.
#
# FAIL-OPEN POLICY (deliberate): if stdin is empty, is not valid JSON, or this script
# throws an unexpected error, it exits 0 (allow). Reason: this hook is one layer of
# two. The permissions.deny rules in settings.json are the static second layer, and a
# buggy hook that blocks every command would stall every agent in the project. The
# trade-off is that a malformed payload cannot be blocked here. Real blocks always use
# exit code 2 with a reason.
#
# This is a best-effort heuristic parser, not a shell interpreter. See
# tests/guardrails/run.ps1 for what is covered and the report for known gaps.

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- helpers

function Norm([string]$s) {
    if ($null -eq $s) { return '' }
    $t = $s.ToLowerInvariant().Replace('\', '/')
    return [regex]::Replace($t, '/{2,}', '/')
}

$script:SteamRx = New-Object System.Text.RegularExpressions.Regex('(?:^|[^a-z0-9])(?:h:|/h|/mnt/h|/cygdrive/h)/steam(?:library|l~\d)(?:/|$|[^a-z0-9_])')
$script:McpRx = New-Object System.Text.RegularExpressions.Regex('(?<![A-Za-z0-9_])--?mcpremote(?![A-Za-z0-9_])', 'IgnoreCase')

function Test-SteamPath([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return $false }
    return $script:SteamRx.IsMatch((Norm $s))
}

function Test-Flag([string]$t) {
    if ($t.Length -gt 1 -and $t.StartsWith('-')) { return $true }
    if ($t -match '^/[A-Za-z?][A-Za-z0-9:=]{0,12}$') { return $true }
    return $false
}

# true if token $t points into the Steam folder (explicitly, or relatively while cwd is inside it)
function Test-SteamTok([string]$t, $ctx) {
    if (Test-SteamPath $t) { return $true }
    if ($ctx.CwdSteam -and -not (Test-Flag $t) -and $t.Length -gt 0 -and $t -notmatch '^([A-Za-z]:|/|\\|~|\$|%)') { return $true }
    return $false
}

function Get-CmdName([string]$t) {
    $n = $t.Replace('\', '/')
    $n = $n.Substring($n.LastIndexOf('/') + 1).ToLowerInvariant()
    return ($n -replace '\.(exe|cmd|bat|com|ps1|sh)$', '')
}

function Block([string]$rule, [string]$msg) {
    return "BLOCKED by project guard rail [$rule]: $msg"
}

# ---------------------------------------------------------------- shell tokenizer

function New-PState {
    return @{
        Segs = (New-Object System.Collections.Generic.List[object])
        Words = (New-Object System.Collections.Generic.List[string])
        Redirs = (New-Object System.Collections.Generic.List[string])
        Sb = (New-Object System.Text.StringBuilder)
        Has = $false; RedirNext = $false; SkipNext = $false; Pipe = $false
    }
}

function Flush-Word($P) {
    if ($P.Has) {
        $w = $P.Sb.ToString()
        if ($P.RedirNext) { [void]$P.Redirs.Add($w); $P.RedirNext = $false }
        elseif ($P.SkipNext) { $P.SkipNext = $false }
        else { [void]$P.Words.Add($w) }
    }
    [void]$P.Sb.Clear()
    $P.Has = $false
}

function End-Seg($P, [bool]$nextPipe) {
    Flush-Word $P
    if ($P.Words.Count -gt 0 -or $P.Redirs.Count -gt 0) {
        [void]$P.Segs.Add([pscustomobject]@{ Words = $P.Words.ToArray(); Redirs = $P.Redirs.ToArray(); Pipe = $P.Pipe })
    }
    $P.Words.Clear(); $P.Redirs.Clear()
    $P.RedirNext = $false; $P.SkipNext = $false
    $P.Pipe = $nextPipe
}

# Quote-aware split into segments (separated by ; | && || & newline and parentheses).
function Parse-Shell([string]$text) {
    $P = New-PState
    $n = $text.Length
    $i = 0
    $q = ''
    while ($i -lt $n) {
        $c = [string]$text[$i]
        if ($q -ne '') {
            if ($c -eq $q) { $q = '' } else { [void]$P.Sb.Append($c) }
            $i++
            continue
        }
        if ($c -eq '"' -or $c -eq "'") { $q = $c; $P.Has = $true }
        elseif ($c -eq ' ' -or $c -eq "`t") { Flush-Word $P }
        elseif ($c -eq "`n" -or $c -eq "`r" -or $c -eq ';') { End-Seg $P $false }
        elseif ($c -eq '|') {
            if ($i + 1 -lt $n -and $text[$i + 1] -eq '|') { $i++; End-Seg $P $false } else { End-Seg $P $true }
        }
        elseif ($c -eq '&') {
            if ($i + 1 -lt $n -and $text[$i + 1] -eq '&') { $i++; End-Seg $P $false }
            elseif ($P.Words.Count -eq 0 -and -not $P.Has) { }   # PowerShell call operator
            else { End-Seg $P $false }
        }
        elseif ($c -eq '>') {
            if ($P.Has -and $P.Sb.ToString() -match '^\d+$') { [void]$P.Sb.Clear(); $P.Has = $false } else { Flush-Word $P }
            if ($i + 1 -lt $n -and ($text[$i + 1] -eq '>' -or $text[$i + 1] -eq '&' -or $text[$i + 1] -eq '|')) { $i++ }
            $P.RedirNext = $true
        }
        elseif ($c -eq '<') {
            Flush-Word $P
            while ($i + 1 -lt $n -and $text[$i + 1] -eq '<') { $i++ }
            $P.SkipNext = $true
        }
        elseif ($c -eq '(' -or $c -eq ')') { End-Seg $P $false }
        elseif ($c -eq '`') {
            if ($i + 1 -lt $n) { $i++; [void]$P.Sb.Append($text[$i]); $P.Has = $true }
        }
        else { [void]$P.Sb.Append($c); $P.Has = $true }
        $i++
    }
    End-Seg $P $false
    return ,$P.Segs.ToArray()
}

# Removes bash heredoc bodies and PowerShell here-strings (their content is data, not commands).
# Returns @{ Text = stripped text; Bodies = list of removed bodies }.
function Strip-Bodies([string]$text) {
    $bodies = New-Object System.Collections.Generic.List[string]
    $heredoc = New-Object System.Text.RegularExpressions.Regex('(?s)<<-?[ \t]*([''"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\r\n]*)\r?\n(.*?)\r?\n[ \t]*\2[ \t]*(?=\r?\n|$)')
    $text = $heredoc.Replace($text, {
        param($m)
        [void]$bodies.Add($m.Groups[4].Value)
        return '<<' + $m.Groups[2].Value + $m.Groups[3].Value
    })
    $here = New-Object System.Text.RegularExpressions.Regex('(?s)@([''"])[ \t]*\r?\n(.*?)\r?\n\1@')
    $text = $here.Replace($text, {
        param($m)
        [void]$bodies.Add($m.Groups[2].Value)
        return "'(here-string)'"
    })
    return @{ Text = $text; Bodies = $bodies }
}

# ---------------------------------------------------------------- git helpers

function Invoke-Git([string]$dir, [string[]]$gitArgs) {
    try {
        if (-not $dir -or -not (Test-Path -LiteralPath $dir)) { $dir = $script:ProjectRoot }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = 'git'
        $parts = New-Object System.Collections.Generic.List[string]
        foreach ($a in $gitArgs) {
            if ($a -match '[\s"]' -or $a -eq '') { [void]$parts.Add('"' + ($a -replace '"', '\"') + '"') } else { [void]$parts.Add($a) }
        }
        $psi.Arguments = ($parts -join ' ')
        $psi.WorkingDirectory = $dir
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $p = [System.Diagnostics.Process]::Start($psi)
        $errTask = $p.StandardError.ReadToEndAsync()
        $out = $p.StandardOutput.ReadToEnd()
        [void]$p.WaitForExit(15000)
        if ($p.ExitCode -ne 0) { return $null }
        return $out
    }
    catch { return $null }
}

function Get-CurrentBranch([string]$dir) {
    $o = Invoke-Git $dir @('symbolic-ref', '--short', '-q', 'HEAD')
    if ($null -eq $o) { return '' }
    return $o.Trim()
}

# ---------------------------------------------------------------- secrets

$script:SecretPatterns = @(
    @{ N = 'Mapillary token'; R = 'MLY\|\d{6,}\|[A-Za-z0-9]{16,}' },
    @{ N = 'GitHub token'; R = '(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})' },
    @{ N = 'Anthropic API key'; R = 'sk-ant-[A-Za-z0-9_\-]{20,}' },
    @{ N = 'private key block'; R = '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----' }
)

function Find-Secret([string]$text) {
    if ([string]::IsNullOrEmpty($text)) { return $null }
    foreach ($p in $script:SecretPatterns) {
        if ([regex]::IsMatch($text, $p.R)) { return $p.N }
    }
    return $null
}

function Test-SecretName([string]$path) {
    $leaf = ($path.Replace('\', '/').TrimEnd('/'))
    $leaf = $leaf.Substring($leaf.LastIndexOf('/') + 1).ToLowerInvariant()
    if ($leaf -eq '.env') { return $true }
    if ($leaf -match '^\.env\.' -and $leaf -notmatch '^\.env\.(example|sample|template|dist)$') { return $true }
    if ($leaf -match '^secrets?\.env$') { return $true }
    if ($leaf -match '\.(pem|key|pfx|p12)$') { return $true }
    if ($leaf -match '^id_(rsa|dsa|ecdsa|ed25519)$') { return $true }
    return $false
}

function Scan-File([string]$full) {
    try {
        $fi = New-Object System.IO.FileInfo($full)
        if (-not $fi.Exists) { return $null }
        if ($fi.Length -gt 524288) { return $null }
        if ($fi.Extension -match '^\.(png|jpg|jpeg|gif|webp|bmp|dds|zip|7z|gz|exe|dll|bin|pdf|tif|tiff|laz|las|pbf|bz2)$') { return $null }
        return (Find-Secret ([System.IO.File]::ReadAllText($full)))
    }
    catch { return $null }
}

function Get-AddedLines([string]$diff) {
    if ([string]::IsNullOrEmpty($diff)) { return '' }
    $sb = New-Object System.Text.StringBuilder
    foreach ($m in [regex]::Matches($diff, '(?m)^\+(?!\+\+).*$')) { [void]$sb.AppendLine($m.Value) }
    return $sb.ToString()
}

# git add / git commit
function Test-GitSecrets([string]$sub, [string[]]$gargs, [string]$dir) {
    $paths = New-Object System.Collections.Generic.List[string]
    $all = $false; $force = $false; $commitAll = $false
    $valueFlags = @('-m', '-F', '-C', '-c', '--message', '--file', '--author', '--date', '--reuse-message', '--reedit-message', '--template', '-t', '--cleanup', '--pathspec-from-file')
    for ($k = 0; $k -lt $gargs.Count; $k++) {
        $a = $gargs[$k]
        if ($a -eq '--') { continue }
        if ($sub -eq 'commit' -and $valueFlags -contains $a) { $k++; continue }
        if ($a -match '^--(all|update)$' -or $a -eq '-A' -or $a -eq '-u') { $all = $true; if ($sub -eq 'commit') { $commitAll = $true }; continue }
        if ($a -match '^--force$' -or $a -eq '-f') { $force = $true; continue }
        if ($sub -eq 'commit' -and ($a -match '^-[A-Za-z]*a[A-Za-z]*$' -or $a -eq '--all')) { $commitAll = $true }
        if ($a.StartsWith('-')) { continue }
        [void]$paths.Add($a)
    }
    $label = "git $sub"

    # explicit names
    foreach ($p in $paths) {
        if (Test-SecretName $p) {
            return (Block 'no-secrets (guard rail 6)' "$label names a secret-looking file ('$p'). Secret files (.env, secrets.env, *.pem, *.key) must never be committed; the repository is public.")
        }
    }

    if ($sub -eq 'add') {
        $spec = New-Object System.Collections.Generic.List[string]
        if ($paths.Count -eq 0 -and $all) { [void]$spec.Add('.') } else { foreach ($p in $paths) { [void]$spec.Add($p) } }
        if ($spec.Count -eq 0) { return $null }
        $ls = @('ls-files', '-m', '-o', '--exclude-standard', '-z', '--') + $spec.ToArray()
        $out = Invoke-Git $dir $ls
        $files = New-Object System.Collections.Generic.List[string]
        if ($out) { foreach ($f in $out.Split([char]0)) { if ($f) { [void]$files.Add($f) } } }
        foreach ($p in $paths) {
            $fp = $p
            if (-not [System.IO.Path]::IsPathRooted($fp)) { $fp = Join-Path $dir $p }
            if ((Test-Path -LiteralPath $fp -PathType Leaf) -and -not $files.Contains($p)) { [void]$files.Add($p) }
        }
        $cnt = 0
        foreach ($f in $files) {
            $cnt++
            if ($cnt -gt 2000) { break }
            if (Test-SecretName $f) {
                return (Block 'no-secrets (guard rail 6)' "git add would stage '$f', which is secret-named. Add it to .gitignore instead.")
            }
            $full = $f
            if (-not [System.IO.Path]::IsPathRooted($full)) { $full = Join-Path $dir $f }
            $hit = Scan-File $full
            if ($hit) {
                return (Block 'no-secrets (guard rail 6)' "git add would stage '$f', which contains a $hit. Remove the secret and keep it outside the repository.")
            }
        }
        return $null
    }

    # commit: staged names + staged diff (+ unstaged tracked changes with -a, + explicit paths)
    $names = Invoke-Git $dir @('diff', '--cached', '--name-only', '--diff-filter=ACMRT', '-z')
    if ($commitAll) {
        $n2 = Invoke-Git $dir @('diff', '--name-only', '--diff-filter=ACMRT', '-z')
        $names = '' + $names + [char]0 + $n2
    }
    if ($names) {
        foreach ($f in $names.Split([char]0)) {
            if ($f -and (Test-SecretName $f)) {
                return (Block 'no-secrets (guard rail 6)' "git commit would include secret-named file '$f'. Unstage it (git rm --cached) and add it to .gitignore.")
            }
        }
    }
    $diff = Invoke-Git $dir @('diff', '--cached', '-U0', '--no-color', '--text', '--diff-filter=ACMRT')
    $hit = Find-Secret (Get-AddedLines $diff)
    if (-not $hit -and $commitAll) {
        $diff2 = Invoke-Git $dir @('diff', '-U0', '--no-color', '--text', '--diff-filter=ACMRT')
        $hit = Find-Secret (Get-AddedLines $diff2)
    }
    if (-not $hit) {
        foreach ($p in $paths) {
            $fp = $p
            if (-not [System.IO.Path]::IsPathRooted($fp)) { $fp = Join-Path $dir $p }
            $hit = Scan-File $fp
            if ($hit) { break }
        }
    }
    if ($hit) {
        return (Block 'no-secrets (guard rail 6)' "git commit would include content that looks like a $hit. Unstage it, remove the secret and keep it outside the repository.")
    }
    return $null
}

# git push
function Test-GitPush([string[]]$gargs, [string]$dir) {
    $pos = New-Object System.Collections.Generic.List[string]
    for ($k = 0; $k -lt $gargs.Count; $k++) {
        $a = $gargs[$k]
        if ($a -match '^--(force|force-with-lease|force-if-includes)(=.*)?$' -or $a -cmatch '^-[A-Za-z]*f[A-Za-z]*$') {
            return (Block 'no-push-main (guard rail 5)' "force pushes are not allowed ('$a').")
        }
        if ($a -eq '--all' -or $a -eq '--mirror') {
            return (Block 'no-push-main (guard rail 5)' "'git push $a' would update main. Push the dev branch explicitly: git push origin dev.")
        }
        if (@('-o', '--push-option', '--repo', '--receive-pack', '--exec') -contains $a) { $k++; continue }
        if ($a.StartsWith('-')) { continue }
        [void]$pos.Add($a)
    }
    if ($pos.Count -ge 1 -and $pos[0].ToLowerInvariant() -eq 'main') {
        return (Block 'no-push-main (guard rail 5)' 'never push to main. Push the dev branch: git push origin dev.')
    }
    $needBranch = $false
    if ($pos.Count -le 1) { $needBranch = $true }
    for ($k = 1; $k -lt $pos.Count; $k++) {
        $spec = $pos[$k]
        if ($spec.StartsWith('+')) {
            return (Block 'no-push-main (guard rail 5)' "forced refspec '$spec' is not allowed.")
        }
        if ($spec -eq ':' -or $spec.Contains('*')) {
            return (Block 'no-push-main (guard rail 5)' "wildcard or matching refspec '$spec' could update main. Name the branch explicitly.")
        }
        $dst = $spec
        $src = $spec
        $ci = $spec.LastIndexOf(':')
        if ($ci -ge 0) { $dst = $spec.Substring($ci + 1); $src = $spec.Substring(0, $ci) }
        $dstN = ($dst -replace '^refs/heads/', '').ToLowerInvariant()
        if ($dstN -eq 'main') {
            return (Block 'no-push-main (guard rail 5)' "refspec '$spec' targets main. Never push to main; push dev instead.")
        }
        if ($ci -lt 0 -and ($src -eq 'HEAD' -or $src -eq '@')) { $needBranch = $true }
        if ($ci -ge 0 -and $src -eq '' -and $dstN -eq 'main') { return (Block 'no-push-main (guard rail 5)' 'deleting main is not allowed.') }
    }
    if ($needBranch) {
        $br = Get-CurrentBranch $dir
        if ($br.ToLowerInvariant() -eq 'main') {
            return (Block 'no-push-main (guard rail 5)' 'the current branch is main, so this push would update main. Switch to dev first.')
        }
    }
    return $null
}

# ---------------------------------------------------------------- segment analysis

$script:Wrappers = @('sudo', 'env', 'nohup', 'time', 'command', 'builtin', 'exec', 'call', 'do', 'then', 'else', 'elif', 'if', 'while', 'until', '!', '{', '}', 'start', 'nice')
$script:Destructive = @('rm', 'del', 'erase', 'rmdir', 'rd', 'remove-item', 'ri', 'mv', 'move', 'move-item', 'mi', 'mkdir', 'md', 'new-item', 'ni', 'set-content', 'sc', 'add-content', 'ac', 'clear-content', 'clc', 'out-file', 'tee', 'tee-object', 'set-itemproperty', 'sp', 'remove-itemproperty', 'rename-item', 'ren', 'rni', 'touch', 'truncate', 'shred', 'chmod', 'chown', 'ln', 'mklink', 'set-acl', 'export-csv', 'export-clixml', 'set-item', 'unlink', 'new-psdrive', 'subst', 'fsutil', 'install-item')
$script:PipeVictims = @('rm', 'del', 'erase', 'rmdir', 'rd', 'remove-item', 'ri', 'mv', 'move', 'move-item', 'mi', 'rename-item', 'ren', 'rni', 'set-itemproperty', 'clear-content', 'set-acl', 'remove-itemproperty')
$script:CopyType = @('cp', 'copy', 'copy-item', 'cpi', 'xcopy', 'robocopy', 'install', 'rsync', 'scp', 'expand-archive', 'compress-archive', 'ditto')
$script:Downloaders = @('curl', 'wget', 'invoke-webrequest', 'iwr', 'invoke-restmethod', 'irm', 'start-bitstransfer', 'certutil', 'bitsadmin')
$script:ReadOnlyGit = @('status', 'log', 'diff', 'show', 'ls-files', 'ls-tree', 'rev-parse', 'rev-list', 'blame', 'grep', 'describe', 'cat-file', 'shortlog', 'version', 'help', 'ls-remote', 'check-ignore', 'name-rev', 'for-each-ref', 'count-objects', 'whatchanged', 'diff-tree', 'show-ref', 'remote', 'config')
$script:BenignForMcp = @('rg', 'grep', 'egrep', 'fgrep', 'ag', 'ack', 'findstr', 'select-string', 'sls', 'echo', 'write-output', 'write-host', 'printf', 'git', 'gh', 'cat', 'type', 'get-content', 'gc', 'head', 'tail', 'less', 'more', 'wc', 'ls', 'dir', 'get-childitem', 'gci', 'test-path')
$script:Interpreters = @('python', 'python3', 'py', 'node', 'nodejs', 'perl', 'ruby', 'php', 'lua', 'luajit', 'deno', 'bun')

function Get-CopyDest([string]$cmd, [string[]]$cargs) {
    $pos = New-Object System.Collections.Generic.List[string]
    $dest = $null
    $namedSrc = $false
    $destFlags = @('destination', 'destinationpath', 'dest', 't')
    $valFlags = @('filter', 'include', 'exclude', 'erroraction', 'ea', 'credential', 'pathtype', 'stream')
    for ($k = 0; $k -lt $cargs.Count; $k++) {
        $a = $cargs[$k]
        if (Test-Flag $a) {
            $name = $a.TrimStart('-').ToLowerInvariant()
            if ($name -match '^target-directory=(.*)$') { $dest = $Matches[1] }
            elseif ($name -eq 'target-directory' -and $k + 1 -lt $cargs.Count) { $dest = $cargs[$k + 1]; $k++ }
            elseif ($destFlags -contains $name -and $k + 1 -lt $cargs.Count) { $dest = $cargs[$k + 1]; $k++ }
            elseif (($name -eq 'path' -or $name -eq 'literalpath') -and $k + 1 -lt $cargs.Count) { $namedSrc = $true; $k++ }
            elseif ($valFlags -contains $name -and $k + 1 -lt $cargs.Count) { $k++ }
            continue
        }
        [void]$pos.Add($a)
    }
    if ($dest) { return $dest }
    if ($namedSrc -and $pos.Count -ge 1) { return $pos[0] }
    if ($cmd -eq 'robocopy' -and $pos.Count -ge 2) { return $pos[1] }
    if (@('copy-item', 'cpi', 'expand-archive', 'compress-archive') -contains $cmd -and $pos.Count -ge 2) { return $pos[1] }
    if ($pos.Count -ge 2) { return $pos[$pos.Count - 1] }
    return $null
}

# Returns the command string a shell launcher will run, or $null.
function Get-InnerCommand([string]$cmd, [string[]]$cargs) {
    if ($cmd -match '^(bash|sh|zsh|dash|ksh)$') {
        for ($k = 0; $k -lt $cargs.Count; $k++) {
            if ($cargs[$k] -cmatch '^-[a-z]*c$' -and $k + 1 -lt $cargs.Count) { return ($cargs[($k + 1)..($cargs.Count - 1)] -join ' ') }
        }
        return $null
    }
    if ($cmd -eq 'cmd') {
        for ($k = 0; $k -lt $cargs.Count; $k++) {
            if ($cargs[$k] -match '^/[ckCK]$' -and $k + 1 -lt $cargs.Count) { return ($cargs[($k + 1)..($cargs.Count - 1)] -join ' ') }
        }
        return $null
    }
    if ($cmd -eq 'powershell' -or $cmd -eq 'pwsh') {
        for ($k = 0; $k -lt $cargs.Count; $k++) {
            $a = $cargs[$k].ToLowerInvariant()
            if ($a -match '^-(c|com|comm|comma|comman|command)$' -and $k + 1 -lt $cargs.Count) { return ($cargs[($k + 1)..($cargs.Count - 1)] -join ' ') }
            if ($a -match '^-(e|ec|en|enc|enco|encod|encode|encoded|encodedc|encodedco|encodedcom|encodedcomm|encodedcomma|encodedcomman|encodedcommand)$' -and $k + 1 -lt $cargs.Count) {
                try { return [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($cargs[$k + 1])) } catch { return $null }
            }
        }
        return $null
    }
    if ($cmd -match '^(iex|invoke-expression|eval)$') { return ($cargs -join ' ') }
    if ($cmd -eq 'wsl') {
        $k = 0
        while ($k -lt $cargs.Count -and $cargs[$k].StartsWith('-')) { $k++ }
        if ($k -lt $cargs.Count) { return ($cargs[$k..($cargs.Count - 1)] -join ' ') }
    }
    return $null
}

function Analyze-Segment($seg, [bool]$carry, $ctx, [int]$depth) {
    # 1. redirections into the Steam folder
    foreach ($t in $seg.Redirs) {
        if ($t -notmatch '^(/dev/null|nul|\$null)$' -and (Test-SteamTok $t $ctx)) {
            return (Block 'steam-write (guard rail 1)' "output redirection into the Steam game folder ('$t'). H:\SteamLibrary is read-only reference material.")
        }
    }
    $w = @($seg.Words)
    # 2. command substitution inside quoted words
    foreach ($x in $w) {
        if ($x.Contains('$(')) {
            $m = [regex]::Match($x, '\$\((.*)\)')
            if ($m.Success -and $depth -lt 3) {
                $r = Analyze-String $m.Groups[1].Value $ctx ($depth + 1)
                if ($r) { return $r }
            }
        }
    }
    # 3. strip wrappers / env assignments
    $i = 0
    while ($i -lt $w.Count) {
        $tl = $w[$i].ToLowerInvariant()
        if ($script:Wrappers -contains $tl) {
            $i++
            if (@('if', 'while', 'until', 'do', 'then', 'else', 'elif', '!', '{', '}') -notcontains $tl) {
                while ($i -lt $w.Count -and $w[$i].StartsWith('-')) { $i++ }
            }
            continue
        }
        if ($w[$i] -match '^[A-Za-z_][A-Za-z0-9_]*=') { $i++; continue }
        break
    }
    if ($i -ge $w.Count) { return $null }
    $cmd = Get-CmdName $w[$i]
    $cargs = @()
    if ($i + 1 -lt $w.Count) { $cargs = @($w[($i + 1)..($w.Count - 1)]) }

    # 4. cd tracking (relative writes after cd into Steam are writes into Steam)
    if (@('cd', 'chdir', 'set-location', 'sl', 'pushd', 'push-location') -contains $cmd) {
        foreach ($a in $cargs) {
            if (Test-Flag $a) { continue }
            if (Test-SteamPath $a) { $ctx.CwdSteam = $true }
            elseif ($a -match '^([A-Za-z]:|/|\\|~)') { $ctx.CwdSteam = $false }
            break
        }
        return $null
    }

    # 5. xargs: analyze the command it runs, with the piped-in context
    if ($cmd -eq 'xargs') {
        $k = 0
        while ($k -lt $cargs.Count -and $cargs[$k].StartsWith('-')) {
            if (@('-I', '-n', '-L', '-P', '-d', '-E', '-s', '-a') -ccontains $cargs[$k]) { $k++ }
            $k++
        }
        if ($k -lt $cargs.Count) {
            $pseudo = [pscustomobject]@{ Words = @($cargs[$k..($cargs.Count - 1)]); Redirs = @(); Pipe = $true }
            return (Analyze-Segment $pseudo $carry $ctx $depth)
        }
        return $null
    }
    # shell launchers: analyze the inner command line
    if ($depth -lt 3) {
        $inner = Get-InnerCommand $cmd $cargs
        if ($inner) {
            $r = Analyze-String $inner $ctx ($depth + 1)
            if ($r) { return $r }
        }
    }

    # 6. rule 4: -mcpremote as an argument of anything that is not a search/echo/git/gh command
    if ($script:BenignForMcp -notcontains $cmd) {
        foreach ($a in $cargs) {
            if ($script:McpRx.IsMatch($a)) {
                return (Block 'no-mcpremote (guard rail 4)' "'-mcpremote' must never be passed to BeamNG (its MCP server has no login). To describe the flag in docs use the Write tool on a .md file.")
            }
        }
    }

    # 7. rule 1: Steam folder
    $anySteam = $false
    foreach ($a in $cargs) {
        if (-not (Test-Flag $a) -and (Test-SteamTok $a $ctx)) { $anySteam = $true; break }
    }
    $steamMsg = { param($what) Block 'steam-write (guard rail 1)' "$what the Steam game folder. H:\SteamLibrary is read-only reference material; reading and copying FROM it is fine." }

    if ($script:Destructive -contains $cmd) {
        if ($anySteam) { return (& $steamMsg "'$cmd' would modify") }
        if ($carry -and $seg.Pipe -and ($script:PipeVictims -contains $cmd)) { return (& $steamMsg "'$cmd' on piped items from") }
    }
    if ($script:CopyType -contains $cmd) {
        $dest = Get-CopyDest $cmd $cargs
        if ($dest -and (Test-SteamTok $dest $ctx)) { return (& $steamMsg "'$cmd' would write into") }
    }
    if ($script:Downloaders -contains $cmd -and $anySteam) { return (& $steamMsg "'$cmd' would download into") }
    if ($cmd -match '^(unzip|tar|bsdtar|7z|7za|7zr|expand|jar)$') {
        for ($k = 0; $k -lt $cargs.Count; $k++) {
            $a = $cargs[$k]
            $nx = ''
            if ($k + 1 -lt $cargs.Count) { $nx = $cargs[$k + 1] }
            if (@('-d', '-C', '--directory', '-o', '-O', '--output-directory') -ccontains $a -and (Test-SteamTok $nx $ctx)) { return (& $steamMsg "'$cmd' would extract into") }
            if ($a -cmatch '^(-o|-O|-C|--directory=)(.+)$' -and (Test-SteamPath $Matches[2])) { return (& $steamMsg "'$cmd' would extract into") }
            if ($cmd -match '^(tar|bsdtar)$' -and $a -cmatch '^-?[A-Za-z]*c[A-Za-z]*f[A-Za-z]*$' -and (Test-SteamTok $nx $ctx)) { return (& $steamMsg "'$cmd' would create an archive in") }
        }
        if ($cmd -match '^7z' -and $cargs.Count -ge 2 -and @('a', 'u', 'd', 'rn') -ccontains $cargs[0]) {
            foreach ($a in $cargs[1..($cargs.Count - 1)]) { if (-not (Test-Flag $a)) { if (Test-SteamTok $a $ctx) { return (& $steamMsg "'7z' would write an archive in") }; break } }
        }
    }
    if ($cmd -match '^(sed|perl)$' -and $anySteam) {
        foreach ($a in $cargs) { if ($a -cmatch '^-[A-Za-z]*i' -or $a -eq '--in-place') { return (& $steamMsg "'$cmd -i' would edit a file in") } }
    }
    if ($cmd -eq 'dd') {
        foreach ($a in $cargs) { if ($a -match '^of=(.*)$' -and (Test-SteamTok $Matches[1] $ctx)) { return (& $steamMsg "'dd of=' would write into") } }
    }
    if ($cmd -eq 'find' -and $anySteam) {
        foreach ($a in $cargs) { if (@('-delete', '-exec', '-execdir', '-ok', '-fprint', '-fprintf', '-fls') -contains $a) { return (& $steamMsg "'find $a' would modify") } }
    }
    if ($cmd -match '^(icacls|cacls|attrib|takeown)$' -and $anySteam) {
        foreach ($a in $cargs) {
            if ($cmd -eq 'takeown' -or $a -match '^/(grant|deny|remove|setowner|reset|inheritance|save|restore)' -or $a -match '^[+-][rhsaip]$') { return (& $steamMsg "'$cmd' would change permissions or attributes in") }
        }
    }
    if ($script:Interpreters -contains $cmd -and $anySteam) {
        $hasInline = $false
        foreach ($a in $cargs) { if ($a -ceq '-c' -or $a -ceq '-e' -or $a -ceq '-E' -or $a -ceq '-p') { $hasInline = $true } }
        if ($hasInline -and (($cargs -join ' ') -match '(?i)open\s*\(|write|copy|move|rename|replace|remove|unlink|rmtree|rmdir|mkdir|makedirs|truncate|save|dump|append|touch|symlink|chmod|fs\.')) {
            return (& $steamMsg "inline '$cmd' code appears to write into")
        }
    }

    # 8. git / gh
    if ($cmd -eq 'git') {
        $gdir = $ctx.Cwd
        $sub = $null
        $k = 0
        while ($k -lt $cargs.Count) {
            $a = $cargs[$k]
            if ($a -ceq '-C' -and $k + 1 -lt $cargs.Count) {
                $d = $cargs[$k + 1]
                if (-not [System.IO.Path]::IsPathRooted($d)) { $d = Join-Path $gdir $d }
                $gdir = $d; $k += 2; continue
            }
            if (@('-c', '--exec-path', '--namespace', '--super-prefix', '--config-env') -ccontains $a) { $k += 2; continue }
            if ($a -match '^--git-dir$|^--work-tree$') { $k += 2; continue }
            if ($a.StartsWith('-')) { $k++; continue }
            $sub = $a.ToLowerInvariant()
            $k++
            break
        }
        if ($null -eq $sub) { return $null }
        $gargs = @()
        if ($k -lt $cargs.Count) { $gargs = @($cargs[$k..($cargs.Count - 1)]) }
        if ((Test-SteamPath $gdir) -or $anySteam) {
            if ($script:ReadOnlyGit -notcontains $sub) { return (& $steamMsg "'git $sub' would modify") }
        }
        if ($sub -eq 'push') { return (Test-GitPush $gargs $gdir) }
        if ($sub -eq 'add' -or $sub -eq 'commit') { return (Test-GitSecrets $sub $gargs $gdir) }
        return $null
    }
    if ($cmd -eq 'gh' -and $cargs.Count -ge 2) {
        $j = ($cargs -join ' ')
        if ($cargs[0] -eq 'pr' -and $cargs[1] -eq 'merge') {
            return (Block 'no-push-main (guard rail 5)' "'gh pr merge' updates main on GitHub. Merging into main is a human decision.")
        }
        if ($cargs[0] -eq 'api' -and $j -match '(?i)git/refs/heads/main|/merges\b|/pulls/\d+/merge\b' -and $j -match '(?i)(-X|--method)[ =]*(POST|PUT|PATCH|DELETE)|\s-[fF]\s|--field|--raw-field|--input') {
            return (Block 'no-push-main (guard rail 5)' "this 'gh api' call would change or merge into main.")
        }
    }
    return $null
}

function Analyze-String([string]$text, $ctx, [int]$depth) {
    if ($depth -gt 3 -or [string]::IsNullOrWhiteSpace($text)) { return $null }
    $segs = Parse-Shell $text
    $carry = $false
    foreach ($seg in $segs) {
        $r = Analyze-Segment $seg $carry $ctx $depth
        if ($r) { return $r }
        $carry = Test-SteamPath ($seg.Words -join ' ')
    }
    return $null
}

# Top-level shell command analysis (Bash / PowerShell tools)
function Analyze-Shell([string]$command, [string]$cwd) {
    $ctx = @{ CwdSteam = (Test-SteamPath $cwd); Cwd = $cwd }
    $s = Strip-Bodies $command
    $r = Analyze-String $s.Text $ctx 0
    if ($r) { return $r }

    # .NET file APIs inside PowerShell (the tokenizer cannot see inside the call)
    if ($s.Text -match '(?i)::\s*(write\w*|append\w*|delete|create\w*|replace|setattributes|openwrite)\s*\(' -or $s.Text -match '(?i)new-object\s+(system\.)?io\.(streamwriter|filestream)') {
        if (Test-SteamPath $s.Text) {
            return (Block 'steam-write (guard rail 1)' 'a .NET file API call mentions the Steam game folder. H:\SteamLibrary is read-only reference material.')
        }
    }
    $mv = [regex]::Match($s.Text, '(?i)::\s*(copy|move)\s*\(([^)]*)\)')
    if ($mv.Success) {
        $argsText = $mv.Groups[2].Value
        $parts = $argsText.Split(',')
        $check = $false
        if ($mv.Groups[1].Value.ToLowerInvariant() -eq 'move') { $check = (Test-SteamPath $argsText) }
        elseif ($parts.Count -ge 2) { for ($q = 1; $q -lt $parts.Count; $q++) { if (Test-SteamPath $parts[$q]) { $check = $true } } }
        if ($check) { return (Block 'steam-write (guard rail 1)' 'a .NET File.Copy/Move call would write into the Steam game folder.') }
    }

    # rule 4 for heredoc / here-string bodies: allowed only when the body goes into an exempt file or a git/gh command
    $bodyHit = $false
    foreach ($b in $s.Bodies) { if ($script:McpRx.IsMatch($b)) { $bodyHit = $true } }
    if ($bodyHit) {
        $ok = $false
        $segs = Parse-Shell $s.Text
        foreach ($sg in $segs) {
            if ($sg.Words.Count -gt 0) {
                $c = Get-CmdName $sg.Words[0]
                if ($c -eq 'git' -or $c -eq 'gh') { $ok = $true }
            }
            foreach ($t in $sg.Redirs) { if (Test-McpExemptPath $t $cwd) { $ok = $true } }
        }
        if (-not $ok) {
            return (Block 'no-mcpremote (guard rail 4)' "a heredoc or here-string body contains '-mcpremote' and is not going into a markdown doc, tests/guardrails/ or .claude/hooks/. Never pass or script that flag.")
        }
    }
    return $null
}

# ---------------------------------------------------------------- file tools

function Test-McpExemptPath([string]$path, [string]$cwd) {
    if ([string]::IsNullOrEmpty($path)) { return $false }
    $full = $path
    try {
        if (-not [System.IO.Path]::IsPathRooted($full)) { $full = Join-Path $cwd $full }
        $full = [System.IO.Path]::GetFullPath($full)
    } catch { }
    $n = Norm $full
    $root = (Norm $script:ProjectRoot).TrimEnd('/') + '/'
    if ($n.EndsWith('.md')) { return $true }
    if ($n.StartsWith($root + 'tests/guardrails/') -or $n.StartsWith($root + '.claude/hooks/')) { return $true }
    return $false
}

function Analyze-FileTool([string]$tool, $ti, [string]$cwd) {
    $path = ''
    foreach ($f in @('file_path', 'notebook_path', 'path')) {
        $v = $ti.$f
        if ($v) { $path = [string]$v; break }
    }
    $text = ''
    if ($tool -eq 'Write') { $text = [string]$ti.content }
    elseif ($tool -eq 'Edit') { $text = [string]$ti.new_string }
    elseif ($tool -eq 'NotebookEdit') {
        $ns = $ti.new_source
        if ($ns -is [array]) { $text = ($ns -join "`n") } else { $text = [string]$ns }
    }
    if ($ti.edits) {
        foreach ($e in @($ti.edits)) { $text += "`n" + [string]$e.new_string }
    }

    $full = $path
    if ($path) {
        try {
            if (-not [System.IO.Path]::IsPathRooted($full)) { $full = Join-Path $cwd $full }
            $full = [System.IO.Path]::GetFullPath($full)
        } catch { $full = $path }
    }

    # rule 1
    if ($path -and ((Test-SteamPath $path) -or (Test-SteamPath $full))) {
        return (Block 'steam-write (guard rail 1)' "$tool to '$path' is inside the Steam game folder. H:\SteamLibrary is read-only reference material.")
    }
    # rule 6: secret content inside the repository
    if ($text) {
        $inRepo = $true
        if ($full) {
            $root = (Norm $script:ProjectRoot).TrimEnd('/') + '/'
            $inRepo = (Norm $full).StartsWith($root)
        }
        if ($inRepo) {
            $hit = Find-Secret $text
            if ($hit) {
                return (Block 'no-secrets (guard rail 6)' "the content to write looks like a $hit. The repository is public; keep secrets outside it (for example in an environment variable).")
            }
        }
        # rule 4
        if ($script:McpRx.IsMatch($text) -and -not (Test-McpExemptPath $path $cwd)) {
            return (Block 'no-mcpremote (guard rail 4)' "the content contains '-mcpremote'. That game flag must never be used or scripted. Describing it is allowed only in markdown (.md) docs, tests/guardrails/ and .claude/hooks/. To check for it in code, compare against the bare word without the leading dash.")
        }
    }
    return $null
}

# ---------------------------------------------------------------- main

try {
    $script:ProjectRoot = $env:CLAUDE_PROJECT_DIR
    if (-not $script:ProjectRoot) { $script:ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }

    $stdin = [Console]::OpenStandardInput()
    $ms = New-Object System.IO.MemoryStream
    $stdin.CopyTo($ms)
    $raw = [System.Text.Encoding]::UTF8.GetString($ms.ToArray()).TrimStart([char]0xFEFF)
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }      # fail-open

    try { $j = $raw | ConvertFrom-Json } catch { exit 0 }   # fail-open
    if ($null -eq $j -or -not $j.tool_name -or $null -eq $j.tool_input) { exit 0 }

    $tool = [string]$j.tool_name
    $cwd = [string]$j.cwd
    if (-not $cwd) { $cwd = $script:ProjectRoot }

    $reason = $null
    if ($tool -eq 'Bash' -or $tool -eq 'PowerShell') {
        $cmdText = [string]$j.tool_input.command
        if ($cmdText) { $reason = Analyze-Shell $cmdText $cwd }
    }
    elseif ($tool -eq 'Write' -or $tool -eq 'Edit' -or $tool -eq 'NotebookEdit' -or $tool -eq 'MultiEdit') {
        $reason = Analyze-FileTool $tool $j.tool_input $cwd
    }

    if ($reason) {
        [Console]::Error.WriteLine($reason)
        exit 2
    }
    exit 0
}
catch {
    # fail-open on unexpected internal errors (see policy at top of file)
    [Console]::Error.WriteLine('guard.ps1 internal error (allowed): ' + $_.Exception.Message)
    exit 0
}
