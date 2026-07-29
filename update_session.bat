@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\update_session.py
pause
