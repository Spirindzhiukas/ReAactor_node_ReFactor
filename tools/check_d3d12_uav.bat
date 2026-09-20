@echo off
setlocal EnableExtensions
rem ====================================================================
rem  ANTs D3D12 UAV probe - put this NEXT TO check_d3d12_uav.py (the
rem  pack's tools\ folder is the natural home) and double-click it.
rem
rem  WHY: the native NGX node answered
rem     CreateCommittedResource('nr output', ALLOW_UNORDERED_ACCESS)
rem     -> E_INVALIDARG            device status: healthy
rem  while the SAME call worked in a process where the native node had
rem  run alone. This probe finds out which process state does it, in
rem  four short phases, using the pack's own D3D12 code:
rem     A  fresh device, feature level 11_0   (what the pack asks today)
rem     B  fresh device, feature level 12_0   (what the proven host asks)
rem     C  fresh device after plain CUDA work in this process
rem     D  fresh device after the already-staged legacy engine ran
rem
rem  READ-ONLY: it creates GPU resources, releases them and exits. It
rem  writes nothing to ComfyUI, to models\DLSS or to the staging folder,
rem  and it does not touch ComfyUI. Run it while ComfyUI is CLOSED for
rem  the cleanest answer, or right after a failure for the useful one.
rem
rem  THE VERDICT LINE AT THE END is the answer:
rem    REPRODUCED ...            -> legacy CUDA work in the process is the
rem                                 trigger: run the native node in a fresh
rem                                 ComfyUI process, without the legacy node
rem    a FRESH device already... -> not CUDA: send the whole report
rem    the feature level decides -> send the whole report
rem
rem  ============== THE ONLY BLOCK YOU MAY EDIT ==============
rem  Leave blank for auto-detection. Enable a line only if the report says
rem  something was NOT FOUND (keep the "rem " lowercase when enabling).
rem set "ANTS_DLSS_MODELS=C:\ComfyUI_PORTABLE\ComfyUI\models\DLSS"
rem set "COMFYUI_PORTABLE=C:\ComfyUI_PORTABLE"
rem set "COMFYUI_PYTHON=C:\ComfyUI_PORTABLE\python_embeded\python.exe"
rem  ============== END OF THE BLOCK YOU MAY EDIT ==============

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

set "SCRIPT="
if exist "%HERE%\check_d3d12_uav.py" set "SCRIPT=%HERE%\check_d3d12_uav.py"
if not defined SCRIPT if exist "%HERE%\..\tools\check_d3d12_uav.py" for %%I in ("%HERE%\..\tools\check_d3d12_uav.py") do set "SCRIPT=%%~fI"
if not defined SCRIPT if exist "%HERE%\tools\check_d3d12_uav.py" for %%I in ("%HERE%\tools\check_d3d12_uav.py") do set "SCRIPT=%%~fI"
if not defined SCRIPT echo [X] check_d3d12_uav.py is not here and not in .\tools - keep the two files together & goto fail

set "PYEXE="
set "PYARGS="
if defined COMFYUI_PYTHON if exist "%COMFYUI_PYTHON%" set "PYEXE=%COMFYUI_PYTHON%"
if not defined PYEXE if defined COMFYUI_PORTABLE if exist "%COMFYUI_PORTABLE%\python_embeded\python.exe" set "PYEXE=%COMFYUI_PORTABLE%\python_embeded\python.exe"
if not defined PYEXE for %%D in (C D E F G H I J K) do if not defined PYEXE if exist "%%D:\ComfyUI_PORTABLE\python_embeded\python.exe" set "PYEXE=%%D:\ComfyUI_PORTABLE\python_embeded\python.exe"
if not defined PYEXE for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYEXE set "PYEXE=%%P" & set "PYARGS=-3"
if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul ^| find /i /v "WindowsApps"') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE echo [X] No usable Python found - enable the COMFYUI_PYTHON line at the top of this bat & goto fail

set "DLSSROOT="
if defined ANTS_DLSS_MODELS set "DLSSROOT=%ANTS_DLSS_MODELS%"
if not defined DLSSROOT if defined COMFYUI_PORTABLE if exist "%COMFYUI_PORTABLE%\ComfyUI\models\DLSS" set "DLSSROOT=%COMFYUI_PORTABLE%\ComfyUI\models\DLSS"
if not defined DLSSROOT for %%D in (C D E F G H I J K) do if not defined DLSSROOT if exist "%%D:\ComfyUI_PORTABLE\ComfyUI\models\DLSS" set "DLSSROOT=%%D:\ComfyUI_PORTABLE\ComfyUI\models\DLSS"
if defined DLSSROOT set "ANTS_DLSS_MODELS=%DLSSROOT%"

echo [ANTs] python : %PYEXE% %PYARGS%
echo [ANTs] tool   : %SCRIPT%
echo [ANTs] DLSS   : %DLSSROOT%
echo [ANTs] probing - four phases, no ComfyUI involved...
echo.

"%PYEXE%" %PYARGS% "%SCRIPT%" > "%TEMP%\ants_d3d12_uav.txt" 2>&1
set "RC=%ERRORLEVEL%"
type "%TEMP%\ants_d3d12_uav.txt"
clip < "%TEMP%\ants_d3d12_uav.txt"

echo.
if "%RC%"=="10" echo [!!] REPRODUCED - the legacy CUDA path in the process is the trigger. Send the clipboard text.
if "%RC%"=="11" echo [!!] A fresh device already refuses UAV textures - send the clipboard text.
if "%RC%"=="12" echo [!!] A feature level decides it - send the clipboard text.
if "%RC%"=="13" echo [!!] THE PACK'S CUDA FLAG ARMING IS THE TRIGGER - relaunch ComfyUI with ANTS_NO_CUDA_FLAG_ARM=1 and send the clipboard text.
if "%RC%"=="0" echo [OK] no reproduction in the probe - still send the clipboard text if the node failed.
if "%RC%"=="2" echo [X] the probe could not run - read the lines above & goto fail
echo [OK] the report is on your CLIPBOARD - switch to the chat and press Ctrl+V.
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
