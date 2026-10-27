@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Conversor PDF a Modo Oscuro

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] No se encontro el entorno virtual en .venv.
    echo Asegurate de que la carpeta .venv existe en esta ubicacion.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "invertir_pdf.py" %*

echo.
echo Presiona cualquier tecla para salir...
pause >nul
