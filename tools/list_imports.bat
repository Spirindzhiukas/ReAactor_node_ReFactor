@echo off
setlocal EnableExtensions
rem ====================================================================
rem  ANTs import lister - static peek inside any dll, nothing is executed
rem  Shows which NGX backend a dll binds and which termination APIs it
rem  imports (the silent-kill suspects).
rem
rem  Usage: DRAG ANY DLL ONTO THIS FILE (or run it and paste a path).
rem
rem  ============== THE ONLY BLOCK YOU MAY EDIT ==============
set "PY=C:\ComfyUI_PORTABLE\python_embeded\python.exe"
set "SCRIPT=C:\ComfyUI_PORTABLE\list_imports.py"
rem  ============== END OF THE BLOCK YOU MAY EDIT ==============

if not exist "%PY%" set "PY=python"
set "TARGET=%~1"
if not defined TARGET set /p "TARGET=Drag a dll into this window (or type its full path) and press ENTER: "
if not defined TARGET goto usage
if not exist "%SCRIPT%" echo [X] tool missing: "%SCRIPT%" - copy list_imports.py there, or fix the SCRIPT line in this bat & goto fail
if not exist "%TARGET%" echo [X] dll not found: "%TARGET%" & goto fail

"%PY%" "%SCRIPT%" "%TARGET%" > "%TEMP%\ants_imports.txt" 2>&1
type "%TEMP%\ants_imports.txt"
echo.
echo [OK] result also copied to the clipboard - paste it all back to the chat.
clip < "%TEMP%\ants_imports.txt"
pause
exit /b 0
:usage
echo Drag a dll file onto resolve-style bat, or type its full path.
:fail
pause
exit /b 1
