Write-Host "Starting Backend Server..." -ForegroundColor Green
Set-Location $PSScriptRoot
python -m uvicorn back:app --reload --host 127.0.0.1 --port 8000



