@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ===================================================
echo 🚀 Синхронізація проєкту з GitHub...
echo ===================================================
echo.

if exist ".git_bin\cmd\git.exe" (
    set "GIT_CMD=.git_bin\cmd\git.exe"
) else (
    set "GIT_CMD=git"
)

"%GIT_CMD%" add -A
"%GIT_CMD%" commit -m "Backup / Update: %date% %time%"
"%GIT_CMD%" push origin main

echo.
echo ✅ Проєкт успішно синхронізовано з GitHub!
pause

