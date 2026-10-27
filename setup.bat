@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Установка Эдит ===

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Не найден Python. Установите Python 3.11+ с https://www.python.org/downloads/ ^(галочка "Add to PATH"^) и запустите снова.
  pause & exit /b 1
)

if not exist .venv (
  echo Создаю окружение Python...
  %PY% -m venv .venv || (echo Не получилось создать окружение. & pause & exit /b 1)
)

echo Устанавливаю библиотеки ^(несколько минут, ~2 ГБ вместе с CUDA^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Ошибка установки библиотек. & pause & exit /b 1)

echo Скачиваю запасной голос...
".venv\Scripts\python.exe" download_voice.py

if not exist config.json copy config.example.json config.json >nul

where claude >nul 2>nul || if not exist "%USERPROFILE%\.local\bin\claude.exe" (
  echo.
  echo ВНИМАНИЕ: не найден Claude Code — без него Эдит не сможет думать.
  echo Установите: https://docs.claude.com/en/docs/claude-code и один раз запустите "claude", чтобы войти.
)

echo.
echo Готово! Настройте город в config.json ^(home_city, home_lat, home_lon^) и запустите start.bat
echo Первый запуск скачает модель распознавания речи ^(~1,6 ГБ^).
pause
