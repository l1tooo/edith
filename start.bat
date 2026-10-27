@echo off
rem Запуск Эдит со значком в трее (без окна). Для отладки с журналом в окне — run.bat
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" "%~dp0jarvis.py" --tray
