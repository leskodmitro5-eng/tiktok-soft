@echo off
chcp 65001 > nul
title TikTok Soft - Telegram Bot

echo ===================================================
echo           Запуск TikTok Soft Telegram Bot
echo ===================================================
echo.

:: Перевірка чи встановлено Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ПОМИЛКА] Python не знайдено в системі!
    echo Будь ласка, встановіть Python (3.10+) та поставте галочку "Add Python to PATH".
    pause
    exit /b 1
)

:: Перевірка наявності файлу .env
if not exist .env (
    echo [ПОМИЛКА] Файл .env не знайдено!
    echo Створіть файл .env із необхідними токенами перед запуском.
    pause
    exit /b 1
)

:: Перевірка або створення віртуального середовища venv
if not exist venv (
    echo [ІНІЦІАЛІЗАЦІЯ] Створення віртуального середовища venv...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ПОМИЛКА] Не вдалося створити venv.
        pause
        exit /b 1
    )
)

:: Активація віртуального середовища
call venv\Scripts\activate.bat

:: Встановлення / перевірка залежностей
echo [ОНОВЛЕННЯ] Перевірка та встановлення бібліотек...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [УВАГА] Виникли зауваження під час встановлення залежностей.
)

echo.
echo ===================================================
echo           Бот запускається...
echo  (Щоб зупинити бота, закрийте це вікно або Ctrl+C)
echo ===================================================
echo.

python bot.py

pause
