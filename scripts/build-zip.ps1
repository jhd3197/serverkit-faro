# Builds dist\<name>-<version>.zip from the plugin source.
#
# Usage:
#   .\scripts\build-zip.ps1
#
# Reads plugin.json for name/version, stages files in a temp dir (so dev
# junk like .git, node_modules, dist/ doesn't bloat the bundle), and
# writes dist\<name>-<version>.zip ready for the panel's "Upload Zip"
# install path.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not (Test-Path -LiteralPath 'plugin.json')) {
        throw "plugin.json not found in $root"
    }

    $manifest = Get-Content -Raw -LiteralPath 'plugin.json' | ConvertFrom-Json
    if (-not $manifest.name -or -not $manifest.version) {
        throw "plugin.json must define both 'name' and 'version'"
    }

    $name = $manifest.name
    $version = $manifest.version

    $dist = Join-Path $root 'dist'
    if (-not (Test-Path -LiteralPath $dist)) {
        New-Item -ItemType Directory -Path $dist -Force | Out-Null
    }

    $zipPath = Join-Path $dist "$name-$version.zip"
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }

    # Stage in a temp dir so we don't accidentally include build/dev
    # artifacts. The plugin installer also has its own skip list, but
    # building a clean bundle keeps file counts and audit trails sane.
    $excludeDirs = @(
        '.git', '.github', 'node_modules', '__pycache__', '.venv',
        'venv', 'dist', 'build', '.pytest_cache', '.idea', '.vscode',
        'scripts'
    )
    $excludePatterns = @('*.pyc', '*.pyo', '*.log')

    $staging = Join-Path $env:TEMP "sk-plugin-build-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $staging -Force | Out-Null

    try {
        Get-ChildItem -LiteralPath $root -Force | Where-Object {
            ($excludeDirs -notcontains $_.Name) -and
            ($_.Name -ne '.gitignore' -or $true)  # keep .gitignore (small, useful)
        } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $staging -Recurse -Force
        }

        # Drop excluded patterns inside staging
        foreach ($pat in $excludePatterns) {
            Get-ChildItem -Path $staging -Recurse -Filter $pat -ErrorAction SilentlyContinue |
                Remove-Item -Force -ErrorAction SilentlyContinue
        }

        Compress-Archive -Path "$staging\*" -DestinationPath $zipPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    $size = (Get-Item -LiteralPath $zipPath).Length
    $sizeKb = [math]::Round($size / 1KB, 1)
    Write-Host "Built $zipPath ($sizeKb KB)"
    Write-Host "Upload via panel → Marketplace → Plugins → Upload Zip"
}
finally {
    Pop-Location
}
