<#
.SYNOPSIS
    Idempotent setup for the BeamNG Belfast PC.

.DESCRIPTION
    Ensures: Node.js 22 (OpenJS.NodeJS.22), GitHub CLI (GitHub.cli), uv (astral-sh.uv),
    a uv-managed Python 3.12, the project .venv (uv sync from pyproject.toml), and
    user-level cache locations on H: (UV_CACHE_DIR, UV_PYTHON_INSTALL_DIR, NPM_CONFIG_CACHE).
    Prints tool and library versions. Exits 0 when everything is present, 1 otherwise.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File tools\bootstrap.ps1
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DataRoot = 'H:\BelfastData'
$UvCache = Join-Path $DataRoot 'cache\uv'
$UvPyDir = Join-Path $DataRoot 'uv-python'
$NpmCache = Join-Path $DataRoot 'cache\npm'
$VenvPy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Problems = New-Object System.Collections.Generic.List[string]

function Step([string]$Text) {
    Write-Host ''
    Write-Host "== $Text"
}

function Problem([string]$Text) {
    $script:Problems.Add($Text)
    Write-Host "PROBLEM: $Text"
}

function Refresh-Path {
    # Pick up PATH changes made by winget (Machine + User) without opening a new shell.
    $parts = New-Object System.Collections.Generic.List[string]
    $sources = @()
    $sources += ($env:Path -split ';')
    $sources += ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';')
    $sources += ([Environment]::GetEnvironmentVariable('Path', 'User') -split ';')
    foreach ($p in $sources) {
        if ($p -and -not $parts.Contains($p)) { $parts.Add($p) }
    }
    $env:Path = $parts -join ';'
}

function Get-App([string]$Name) {
    return (Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Install-Winget([string]$Id, [string]$Label) {
    Step "Installing $Label ($Id) with winget"
    $wingetArgs = @('install', '--exact', '--id', $Id, '--source', 'winget',
        '--accept-source-agreements', '--accept-package-agreements', '--silent')
    & winget @wingetArgs
    $code = $LASTEXITCODE
    Refresh-Path
    if ($code -ne 0) {
        Problem "winget install of $Id exited with code $code (if it needs elevation, approve the UAC prompt or install it manually)"
    }
}

function Ensure-UserEnv([string]$Name, [string]$Value) {
    $current = [Environment]::GetEnvironmentVariable($Name, 'User')
    if ($current -ne $Value) {
        [Environment]::SetEnvironmentVariable($Name, $Value, 'User')
        Write-Host "Set user env $Name = $Value"
    }
    else {
        Write-Host "User env $Name already = $Value"
    }
    Set-Item -Path "env:$Name" -Value $Value
}

function Ensure-Dir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
        Write-Host "Created $Path"
    }
}

# Tools installed by an earlier run (or another shell) are only visible after a PATH refresh.
Refresh-Path

# ---------------------------------------------------------------- H: storage
Step 'Cache locations on H:'
if (-not (Test-Path -LiteralPath 'H:\')) {
    Problem 'Drive H: is not available; caches cannot be placed there'
}
else {
    Ensure-Dir $DataRoot
    Ensure-Dir $UvCache
    Ensure-Dir $UvPyDir
    Ensure-Dir $NpmCache
    Ensure-UserEnv 'UV_CACHE_DIR' $UvCache
    Ensure-UserEnv 'UV_PYTHON_INSTALL_DIR' $UvPyDir
    Ensure-UserEnv 'NPM_CONFIG_CACHE' $NpmCache
}

# ---------------------------------------------------------------- Node.js 22
Step 'Node.js 22'
$node = Get-App 'node'
$nodeOk = $false
if ($node) {
    $nodeVer = (& node --version)
    if ($nodeVer -match '^v22\.') { $nodeOk = $true }
    Write-Host "Found node $nodeVer"
}
if (-not $nodeOk) {
    Install-Winget 'OpenJS.NodeJS.22' 'Node.js 22 LTS'
    $node = Get-App 'node'
    if ($node -and ((& node --version) -match '^v22\.')) { Write-Host 'Node.js 22 ready' }
    else { Problem 'Node.js 22 not found on PATH after install' }
}

# ---------------------------------------------------------------- npm cache
Step 'npm cache'
$npm = Get-App 'npm.cmd'
if ($npm) {
    $npmCacheNow = (& npm.cmd config get cache)
    if ($npmCacheNow -eq $NpmCache) { Write-Host "npm cache = $npmCacheNow" }
    else { Problem "npm cache is '$npmCacheNow', expected '$NpmCache' (check NPM_CONFIG_CACHE and any .npmrc)" }
}
else {
    Problem 'npm.cmd not found (Node.js install incomplete)'
}

# ---------------------------------------------------------------- GitHub CLI
Step 'GitHub CLI'
if (-not (Get-App 'gh')) {
    Install-Winget 'GitHub.cli' 'GitHub CLI'
}
if (-not (Get-App 'gh')) { Problem 'gh not found on PATH after install' }
else { Write-Host 'Found gh' }

# ---------------------------------------------------------------- uv
Step 'uv'
if (-not (Get-App 'uv')) {
    Install-Winget 'astral-sh.uv' 'uv'
}
if (-not (Get-App 'uv')) {
    Problem 'uv not found on PATH; cannot manage Python or the project environment'
}
else {
    Write-Host "Found $(& uv --version)"
}

# ---------------------------------------------------------------- Python 3.12 via uv
$uvReady = [bool](Get-App 'uv')
if ($uvReady) {
    Step 'Python 3.12 via uv (managed, installed under UV_PYTHON_INSTALL_DIR)'
    $installed = @(& uv python list --only-installed)
    $have312 = $false
    foreach ($line in $installed) {
        if ($line -match '^cpython-3\.12\.\d+' -and $line -like "*$UvPyDir*") { $have312 = $true }
    }
    if ($have312) {
        Write-Host 'Python 3.12 already installed by uv'
    }
    else {
        & uv python install 3.12
        if ($LASTEXITCODE -ne 0) { Problem "uv python install 3.12 failed (exit $LASTEXITCODE)" }
    }
}

# ---------------------------------------------------------------- project venv
if ($uvReady -and (Test-Path -LiteralPath (Join-Path $RepoRoot 'pyproject.toml'))) {
    Step 'Project .venv (uv sync, wheels only)'
    Push-Location $RepoRoot
    try {
        & uv sync --python 3.12 --no-build
        $syncCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($syncCode -ne 0) { Problem "uv sync failed (exit $syncCode); a package may lack a Windows/py3.12 wheel" }
}
elseif ($uvReady) {
    Problem 'pyproject.toml missing at repo root'
}

# ---------------------------------------------------------------- versions
Step 'Versions'
$versionLines = New-Object System.Collections.Generic.List[string]

function Add-Version([string]$Label, [string]$Value) {
    $script:versionLines.Add(('{0,-12} {1}' -f $Label, $Value))
}

if (Get-App 'node') { Add-Version 'node' (& node --version) } else { Add-Version 'node' 'MISSING'; Problem 'node missing' }
if (Get-App 'npm.cmd') { Add-Version 'npm' (& npm.cmd --version) } else { Add-Version 'npm' 'MISSING'; Problem 'npm missing' }
if (Get-App 'gh') { Add-Version 'gh' ((& gh --version) | Select-Object -First 1) } else { Add-Version 'gh' 'MISSING' }
if (Get-App 'uv') { Add-Version 'uv' (& uv --version) } else { Add-Version 'uv' 'MISSING' }

if (Test-Path -LiteralPath $VenvPy) {
    $pyVer = (& $VenvPy --version)
    Add-Version 'python' "$pyVer (.venv)"
    if ($pyVer -notmatch '^Python 3\.12\.') { Problem "venv python is not 3.12: $pyVer" }

    $freeze = @(& uv pip list --python $VenvPy --format freeze)
    $pkgVersions = New-Object System.Collections.Hashtable ([StringComparer]::OrdinalIgnoreCase)
    foreach ($f in $freeze) {
        $parts = $f -split '=='
        if ($parts.Count -eq 2) { $pkgVersions[$parts[0]] = $parts[1] }
    }
    $libs = @(
        @{ Label = 'numpy'; Dist = 'numpy' },
        @{ Label = 'pyproj'; Dist = 'pyproj' },
        @{ Label = 'shapely'; Dist = 'shapely' },
        @{ Label = 'rasterio'; Dist = 'rasterio' },
        @{ Label = 'geopandas'; Dist = 'geopandas' },
        @{ Label = 'osmium'; Dist = 'osmium' },
        @{ Label = 'pyyaml'; Dist = 'PyYAML' },
        @{ Label = 'requests'; Dist = 'requests' },
        @{ Label = 'pillow'; Dist = 'pillow' },
        @{ Label = 'pytest'; Dist = 'pytest' }
    )
    foreach ($lib in $libs) {
        if ($pkgVersions.ContainsKey($lib.Dist)) {
            Add-Version $lib.Label $pkgVersions[$lib.Dist]
        }
        else {
            Add-Version $lib.Label 'MISSING'
            Problem "library $($lib.Label) ($($lib.Dist)) not installed in .venv"
        }
    }
}
else {
    Add-Version 'python' 'MISSING (.venv not created)'
    Problem '.venv python not found'
}

foreach ($l in $versionLines) { Write-Host $l }

Write-Host ''
if ($Problems.Count -eq 0) {
    Write-Host 'Bootstrap OK.'
    exit 0
}
Write-Host "Bootstrap FAILED with $($Problems.Count) problem(s):"
foreach ($p in $Problems) { Write-Host " - $p" }
exit 1
