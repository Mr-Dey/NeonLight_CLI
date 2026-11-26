@echo off
rem Windows launcher: runs lightctl.py with .venv if present, else python on PATH.
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0lightctl.py" %*
) else (
    python "%~dp0lightctl.py" %*
)
