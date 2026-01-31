@echo off
chcp 65001 >nul
echo ========================================
echo   SHORT GRID ORDER 서버 시작
echo ========================================
echo.
echo 브라우저에서 접속: http://localhost:8080/index.html
echo 종료하려면 이 창을 닫으세요.
echo.
cd /d "%~dp0web"
start http://localhost:8080/index.html
python -m http.server 8080
pause
