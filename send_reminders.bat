@echo off
cd /d "%~dp0"
python send_reminders.py >> data\reminders.log 2>&1
