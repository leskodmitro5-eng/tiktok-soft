@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ===================================================
echo 🚀 Синхронізація проєкту з GitHub та Render...
echo ===================================================
echo.

if exist ".git_bin\cmd\git.exe" (
    set "GIT_CMD=.git_bin\cmd\git.exe"
) else (
    set "GIT_CMD=git"
)

"%GIT_CMD%" add -A
"%GIT_CMD%" commit -m "Auto-update: %date% %time%"
"%GIT_CMD%" push origin main --force

echo.
echo ✅ Успішно оновлено! Render автоматично підхопить новий код.
pause

