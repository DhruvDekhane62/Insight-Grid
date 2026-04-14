Write-Host "Starting Landing Page server..." -ForegroundColor Green
Set-Location $PSScriptRoot
python -m http.server 8080 --bind 127.0.0.1 --directory $PSScriptRoot
