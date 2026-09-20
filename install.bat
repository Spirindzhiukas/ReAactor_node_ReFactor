@echo off
setlocal

:: ReActor ReFactor — Windows launcher for install.py
:: It auto-detects the ComfyUI Desktop venv, the portable embedded python,
:: or a PATH python, then runs the safe, interactive dependency bootstrap.

if exist "..\..\.venv\Scripts\python.exe" (
    set "PYTHON=..\..\.venv\Scripts\python.exe"
) else if exist "..\..\..\python_embeded\python.exe" (
    set "PYTHON=..\..\..\python_embeded\python.exe"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ReFactor] No venv python, embedded python, or PATH python found.
        echo            Please install dependencies manually:
        echo            pip install onnx opencv-python onnxruntime
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

echo [ReFactor] Using Python: %PYTHON%
%PYTHON% install.py --yes %*

echo.
echo [ReFactor] Done. You can re-run this any time:
echo            python install.py            (interactive)
echo            python install.py --dry-run  (report only)
@pause
