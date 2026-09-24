@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\start.ps1"
if errorlevel 1 (
    echo.
    echo O sistema foi encerrado com um erro. Veja as mensagens acima.
    pause
)
endlocal
