@echo off
setlocal EnableExtensions
rem ====================================================================
rem  ANTs rig evidence collector - one double-click, one folder to send
rem
rem  WHAT IT DOES (everything below is READ-ONLY):
rem    * prints the DEPLOYED host build marker (the console line
rem      "NR/SR host build ..." must match it - a mismatch means a stale
rem      file ran and the run is void)
rem    * inventories models\DLSS: every staged runtime with size + hash
rem    * copies the NGX core log - staged\ANTs\appdata\logs\nvngx.log - and
rem      any crash file next to it into tools\rig_evidence\<date_time>\files
rem    * records git HEAD, your ANTS_/NVSDK_ variables, the GPU and torch
rem  It NEVER writes to ComfyUI, to models\DLSS or to the staging folder.
rem
rem  AFTER A FAILED RUN: double-click this, then send BOTH the report text
rem  it copies to the clipboard AND the files\ folder it opens.
rem
rem  ============== THE ONLY BLOCK YOU MAY EDIT ==============
rem  Leave blank for auto-detection. Only fill one in if the report says a
rem  folder was NOT FOUND (keep the "rem " lowercase when enabling).
rem set "COMFY_ROOT=C:\ComfyUI_PORTABLE\ComfyUI"
rem set "DLSS_ROOT=C:\ComfyUI_PORTABLE\ComfyUI\models\DLSS"
rem set "COMFYUI_PYTHON=C:\ComfyUI_PORTABLE\python_embeded\python.exe"
rem  ============== END OF THE BLOCK YOU MAY EDIT ==============

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
for %%I in ("%HERE%\..") do set "REPO=%%~fI"

if not exist "%REPO%\tools\collect_rig_evidence.py" echo [X] collect_rig_evidence.py is not in %REPO%\tools - keep the two files together & goto fail

if not defined COMFY_ROOT if exist "%REPO%\..\..\ComfyUI\models" for %%I in ("%REPO%\..\..\ComfyUI") do set "COMFY_ROOT=%%~fI"
if not defined COMFY_ROOT if exist "%REPO%\..\..\models" for %%I in ("%REPO%\..\..") do set "COMFY_ROOT=%%~fI"
if not defined COMFY_ROOT if exist "%REPO%\..\..\..\ComfyUI\models" for %%I in ("%REPO%\..\..\..\ComfyUI") do set "COMFY_ROOT=%%~fI"

set "PYEXE="
set "PYARGS="
if defined COMFYUI_PYTHON if exist "%COMFYUI_PYTHON%" set "PYEXE=%COMFYUI_PYTHON%"
if not defined PYEXE if defined COMFYUI_PORTABLE if exist "%COMFYUI_PORTABLE%\python_embeded\python.exe" set "PYEXE=%COMFYUI_PORTABLE%\python_embeded\python.exe"
if not defined PYEXE if defined COMFY_ROOT if exist "%COMFY_ROOT%\..\python_embeded\python.exe" for %%I in ("%COMFY_ROOT%\..\python_embeded\python.exe") do set "PYEXE=%%~fI"
if not defined PYEXE for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYEXE set "PYEXE=%%P" & set "PYARGS=-3"
if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul ^| find /i /v "WindowsApps"') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE echo [X] No usable Python found - enable the COMFYUI_PYTHON line at the top of this bat & goto fail

"%PYEXE%" %PYARGS% -c "import sys" >nul 2>&1
if errorlevel 1 echo [X] Python at "%PYEXE%" did not respond - fix the COMFYUI_PYTHON line above & goto fail

echo [ANTs] python  : %PYEXE% %PYARGS%
echo [ANTs] nodepack: %REPO%
if defined COMFY_ROOT echo [ANTs] comfy   : %COMFY_ROOT%
if defined DLSS_ROOT echo [ANTs] dlss    : %DLSS_ROOT%
echo [ANTs] collecting - the staged runtime is hashed, this takes a moment...
echo.

"%PYEXE%" %PYARGS% "%REPO%\tools\collect_rig_evidence.py" --repo "%REPO%" --comfy-root "%COMFY_ROOT%" --dlss-root "%DLSS_ROOT%" > "%TEMP%\ants_rig_evidence.txt" 2>&1
set "RC=%ERRORLEVEL%"
type "%TEMP%\ants_rig_evidence.txt"

if not "%RC%"=="0" echo [X] collector exit code %RC% - paste what is above & goto fail
clip < "%TEMP%\ants_rig_evidence.txt"
echo.
echo [OK] the report is on your CLIPBOARD - switch to the chat and press Ctrl+V.
echo [OK] it is also in %REPO%\tools\rig_evidence - the newest date-time folder
echo      there holds the raw logs - send that folder too.
start "" "%REPO%\tools\rig_evidence"
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
