@echo off
rem Windows launcher: runs lightctl.py with the virtual environment in this folder.
"%~dp0.venv\Scripts\python.exe" "%~dp0lightctl.py" %*
