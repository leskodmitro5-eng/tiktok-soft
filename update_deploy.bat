@echo off
chcp 65001 >nul
echo 🚀 Синхронізація проєкту з GitHub та Render...
".git_bin\cmd\git.exe" add -A
".git_bin\cmd\git.exe" commit -m "Auto-update: %date% %time%"
".git_bin\cmd\git.exe" push origin main --force
echo.
echo ✅ Успішно оновлено! Render автоматично підхопить новий код.
pause
