# Prepares a Windows PC to continue Lumi work with Claude Code.
# Installs missing programs, restores skills and plugins from the private kit, and fetches the code.
# Run in PowerShell:
#   irm https://raw.githubusercontent.com/Starlight0307/Team-Build-It/main/docs/windows/setup_claude.ps1 | iex
# Safe to run again. Messages stay ASCII: Windows PowerShell misreads UTF-8 files without a BOM.

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Install-IfMissing($command, $wingetId) {
    if (Get-Command $command -ErrorAction SilentlyContinue) {
        Write-Host "  = $command"
        return $true
    }
    Write-Host "  + installing $wingetId"
    winget install --id $wingetId -e --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (Get-Command $command -ErrorAction SilentlyContinue) { return $true }
    Write-Host "  x $command is still missing. Open a new PowerShell window and run this again." -ForegroundColor Red
    return $false
}

function Sync-Repo($repo, $folder) {
    if (Test-Path (Join-Path $folder '.git')) {
        git -C $folder pull --ff-only
    } else {
        gh repo clone $repo $folder
    }
    return ($LASTEXITCODE -eq 0)
}

function Start-Setup {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'winget was not found. Install "App Installer" from the Microsoft Store, then run this again.' -ForegroundColor Red
        return
    }

    Write-Host "`n[1/5] Programs"
    $ok = (Install-IfMissing 'git' 'Git.Git') -and (Install-IfMissing 'node' 'OpenJS.NodeJS.LTS') -and
          (Install-IfMissing 'gh' 'GitHub.cli') -and (Install-IfMissing 'python' 'Python.Python.3.11')
    if (-not $ok) { return }

    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host '  + installing Claude Code'
        npm install -g @anthropic-ai/claude-code
        Refresh-Path
    }
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host '  x claude is still missing. Open a new PowerShell window and run this again.' -ForegroundColor Red
        return
    }

    Write-Host "`n[2/5] GitHub login"
    gh auth status 2>$null
    if ($LASTEXITCODE -ne 0) { gh auth login }
    gh auth setup-git
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  x GitHub login did not finish. Run this again.' -ForegroundColor Red
        return
    }

    $documents = [Environment]::GetFolderPath('MyDocuments')
    $kit = Join-Path $documents 'claude-code-skills-kit'
    $lumi = Join-Path $documents 'Team-Build-It'

    Write-Host "`n[3/5] Skills and plugins"
    if (-not (Sync-Repo 'Starlight0307/claude-code-skills-kit' $kit)) {
        Write-Host '  x Could not fetch the skills kit. Check that this GitHub account can access it.' -ForegroundColor Red
        return
    }
    Push-Location $kit
    node install.mjs
    $kitOk = ($LASTEXITCODE -eq 0)
    Pop-Location

    Write-Host "`n[4/5] Lumi code"
    if (-not (Sync-Repo 'Starlight0307/Team-Build-It' $lumi)) {
        Write-Host '  x Could not update the Lumi code. Local changes may be in the way; nothing was overwritten.' -ForegroundColor Red
        return
    }

    Write-Host "`n[5/5] Done" -ForegroundColor Green
    if (-not $kitOk) { Write-Host 'Some skills or plugins failed (see the "Failed:" line above). Run this again to retry.' -ForegroundColor Yellow }
    Write-Host "Next: open a new PowerShell window and run"
    Write-Host "  cd `"$lumi`""
    Write-Host '  claude'
}

Start-Setup
