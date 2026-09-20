@echo off
setlocal EnableExtensions
rem ====================================================================
rem  ANTs rig evidence collector - put this NEXT TO collect_rig_evidence.py
rem  (the pack's tools\ folder is the natural home) and double-click it.
rem  It works from anywhere: it finds the pack, ComfyUI and the logs itself.
rem
rem  WHAT IT DOES (everything below is READ-ONLY):
rem    * prints the DEPLOYED host build marker - the console line
rem      "NR/SR host build ..." must match it, otherwise a stale file ran
rem      and the run is void
rem    * inventories models\DLSS: every staged runtime with size + hash
rem    * copies the NGX core log - staged\ANTs\appdata\logs\nvngx.log - and
rem      any crash file next to it into tools\rig_evidence\<date_time>\files
rem    * records git HEAD, your ANTS_/NVSDK_ variables, the GPU and torch
rem  It NEVER writes to ComfyUI, to models\DLSS or to the staging folder.
rem
rem  AFTER A FAILED RUN: send the report it puts on the clipboard AND the
rem  files\ folder it opens.
rem
rem  ============== THE ONLY BLOCK YOU MAY EDIT ==============
rem  Leave blank for auto-detection. Enable a line only if the report says
rem  NOT FOUND (keep the "rem " lowercase when enabling).
rem set "ANTS_EVIDENCE_REPO=I:\AI SHITE\CODING\GITHUB\ReAactor_node_ReFactor"
rem set "ANTS_EVIDENCE_COMFY=C:\ComfyUI_PORTABLE\ComfyUI"
rem set "ANTS_EVIDENCE_DLSS=C:\ComfyUI_PORTABLE\ComfyUI\models\DLSS"
rem set "COMFYUI_PYTHON=C:\ComfyUI_PORTABLE\python_embeded\python.exe"
rem  ============== END OF THE BLOCK YOU MAY EDIT ==============

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

set "SCRIPT="
if exist "%HERE%\collect_rig_evidence.py" set "SCRIPT=%HERE%\collect_rig_evidence.py"
if not defined SCRIPT if exist "%HERE%\..\tools\collect_rig_evidence.py" for %%I in ("%HERE%\..\tools\collect_rig_evidence.py") do set "SCRIPT=%%~fI"
if not defined SCRIPT if exist "%HERE%\tools\collect_rig_evidence.py" for %%I in ("%HERE%\tools\collect_rig_evidence.py") do set "SCRIPT=%%~fI"
if not defined SCRIPT echo [X] collect_rig_evidence.py is not here and not in .\tools - keep the two files together & goto fail

set "PYEXE="
set "PYARGS="
if defined COMFYUI_PYTHON if exist "%COMFYUI_PYTHON%" set "PYEXE=%COMFYUI_PYTHON%"
if not defined PYEXE if defined COMFYUI_PORTABLE if exist "%COMFYUI_PORTABLE%\python_embeded\python.exe" set "PYEXE=%COMFYUI_PORTABLE%\python_embeded\python.exe"
if not defined PYEXE for %%D in (C D E F G H I J K) do if not defined PYEXE if exist "%%D:\ComfyUI_PORTABLE\python_embeded\python.exe" set "PYEXE=%%D:\ComfyUI_PORTABLE\python_embeded\python.exe"
if not defined PYEXE for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYEXE set "PYEXE=%%P" & set "PYARGS=-3"
if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul ^| find /i /v "WindowsApps"') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE echo [X] No usable Python found - enable the COMFYUI_PYTHON line at the top of this bat & goto fail

"%PYEXE%" %PYARGS% -c "import sys" >nul 2>&1
if errorlevel 1 echo [X] Python at "%PYEXE%" did not respond - fix the COMFYUI_PYTHON line above & goto fail

echo [ANTs] python : %PYEXE% %PYARGS%
echo [ANTs] tool   : %SCRIPT%
echo [ANTs] collecting - the staged runtime is hashed, this takes a moment...
echo.

"%PYEXE%" %PYARGS% "%SCRIPT%" > "%TEMP%\ants_rig_evidence.txt" 2>&1
set "RC=%ERRORLEVEL%"
type "%TEMP%\ants_rig_evidence.txt"
clip < "%TEMP%\ants_rig_evidence.txt"

if not "%RC%"=="0" echo [X] the report says something was NOT FOUND - read the lines with [X] or [!] above, enable the matching line in this bat, run again & goto fail

echo.
echo [OK] the report is on your CLIPBOARD - switch to the chat and press Ctrl+V.
echo [OK] the raw logs sit in the newest date-time folder under the pack's
echo      tools\rig_evidence - send that folder too.
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
