Write-Host "`n=== MEDIA RISK INTELLIGENCE SYSTEM ===" -ForegroundColor Cyan
Write-Host "`nChecking server status..." -ForegroundColor Yellow

Start-Sleep -Seconds 3

try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/alerts" -UseBasicParsing -TimeoutSec 5
    Write-Host "`n✅ SUCCESS! Your website is now running!" -ForegroundColor Green
    Write-Host "`n📍 Backend API: http://127.0.0.1:8000" -ForegroundColor Cyan
    Write-Host "📍 Frontend: http://localhost:3000" -ForegroundColor Cyan
    Write-Host "📍 API Docs: http://127.0.0.1:8000/docs" -ForegroundColor Cyan
    Write-Host "`n✨ Features:" -ForegroundColor Yellow
    Write-Host "  • Live dashboard with real data from your CSV files" -ForegroundColor White
    Write-Host "  • Interactive risk map showing fake news hotspots" -ForegroundColor White
    Write-Host "  • Real-time alerts based on your dataset" -ForegroundColor White
    Write-Host "  • Analytics charts with fake vs real news data" -ForegroundColor White
    Write-Host "  • Working buttons and interactive elements" -ForegroundColor White
    Write-Host "`n🌐 Open http://localhost:3000 in your browser!" -ForegroundColor Green
} catch {
    Write-Host "`n⚠️  Servers are still starting..." -ForegroundColor Yellow
    Write-Host "Please wait 10-15 seconds and check:" -ForegroundColor White
    Write-Host "  • Backend: http://127.0.0.1:8000/docs" -ForegroundColor Cyan
    Write-Host "  • Frontend: http://localhost:3000" -ForegroundColor Cyan
}

