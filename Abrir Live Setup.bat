@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Primeira execucao: preparando o ambiente, aguarde...
    py -3.13 -m venv .venv 2>nul || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Falha ao instalar as dependencias.
        pause
        exit /b 1
    )
)
start "" ".venv\Scripts\pythonw.exe" app.py
