# Launch backend, frontend, and landing page servers in separate PowerShell windows
param (
    [switch]$NoLanding
)

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Start-ServiceWindow {
    param(
        [string]$ScriptPath,
        [string]$Title
    )

    if (-not (Test-Path $ScriptPath)) {
        Write-Warning "Script not found: $ScriptPath"
        return
    }

    Start-Process -FilePath powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "`$host.UI.RawUI.WindowTitle = '$Title'; & '$ScriptPath'"
    )
}

Start-ServiceWindow "$scriptRoot\start_backend.ps1" "TrueX Backend"
Start-ServiceWindow "$scriptRoot\start_frontend.ps1" "TrueX Frontend"

if (-not $NoLanding) {
    Start-ServiceWindow "$scriptRoot\start_landing.ps1" "TrueX Landing"
}
else {
    Write-Host "Skipping landing page server (NoLanding switch used)." -ForegroundColor Yellow
}
