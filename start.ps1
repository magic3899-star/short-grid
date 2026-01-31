# SHORT GRID ORDER 서버 시작 (PowerShell)
Write-Host "========================================"
Write-Host "  SHORT GRID ORDER 서버 시작"
Write-Host "========================================"
Write-Host ""
Write-Host "브라우저에서 접속: http://localhost:8080/index.html"
Write-Host "종료하려면 Ctrl+C"
Write-Host ""

Set-Location "$PSScriptRoot\web"
Start-Process "http://localhost:8080/index.html"
python -m http.server 8080
