@echo off
rem Abre o Forja3D sem janela de console.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
start "" ".venv\Scripts\pythonw.exe" -m forja3d
