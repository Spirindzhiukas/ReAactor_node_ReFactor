@echo off
setlocal EnableExtensions
rem ====================================================================
rem  ANTs CUDA multi-GPU check - ComfyUI issue #15255 / CORE-398
rem
rem  Put this NEXT TO check_cuda_multigpu.py (the pack's tools\ folder is
rem  the natural home) and double-click it.
rem
rem  WHAT IT ANSWERS: "is THIS machine affected by the Windows CUDA bug
rem  where host->device copies start failing once a process has touched
rem  more than one GPU?" That bug is what makes ComfyUI fall back to slow
rem  staging, and what can end in a D3D12 device REMOVED / 'cannot match
rem  CUDA ordinal by LUID' in this nodepack.
rem
rem  It WRITES NOTHING and touches no ComfyUI file (READ-ONLY): it only
rem  reads the driver's device list and runs its copy test in its OWN
rem  process - never inside ComfyUI - because that process may end up
rem  with a poisoned CUDA context on purpose.
rem
rem  The verdict line at the end is what to send:
rem    BUG REPRODUCED  -> keep ComfyUI on one GPU: --cuda-device 0 and/or
rem                       --disable-pinned-memory
rem    not reproduced  -> the failure you chase is not this bug (send the
rem                       output anyway, it lists every GPU + LUID)
rem ====================================================================

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

set "SCRIPT="
if exist "%HERE%\check_cuda_multigpu.py" set "SCRIPT=%HERE%\check_cuda_multigpu.py"
if not defined SCRIPT if exist "%HERE%\..\tools\check_cuda_multigpu.py" for %%I in ("%HERE%\..\tools\check_cuda_multigpu.py") do set "SCRIPT=%%~fI"
if not defined SCRIPT if exist "%HERE%\tools\check_cuda_multigpu.py" for %%I in ("%HERE%\tools\check_cuda_multigpu.py") do set "SCRIPT=%%~fI"
if not defined SCRIPT echo [X] check_cuda_multigpu.py is not here and not in .\tools - keep the two files together & goto fail

set "PYEXE="
set "PYARGS="

rem ===== the ONE block you may edit: pin ComfyUI's own python =====
rem Enable the line if the report says the driver is not reachable.
rem set "COMFYUI_PYTHON=C:\ComfyUI_PORTABLE\python_embeded\python.exe"
rem ================================================================

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
echo [ANTs] checking the CUDA device view and the multi-GPU host-copy path...
echo.  runs in its own process; ComfyUI does not need to be closed.
echo.

"%PYEXE%" %PYARGS% "%SCRIPT%" > "%TEMP%\ants_cuda_multigpu.txt" 2>&1
set "RC=%ERRORLEVEL%"
type "%TEMP%\ants_cuda_multigpu.txt"
clip < "%TEMP%\ants_cuda_multigpu.txt"

echo.
if "%RC%"=="10" echo [ANTs] VERDICT: the multi-GPU CUDA bug reproduces on this machine - launch ComfyUI with --cuda-device 0 and/or --disable-pinned-memory.
if "%RC%"=="0" echo [ANTs] VERDICT: not reproduced by this test - read the notes above.
if "%RC%"=="2" echo [ANTs] The check could not run - read the last lines above.
echo.
echo [ANTs] The whole text is on your clipboard - send it as it is.
echo.
pause
exit /b %RC%

:fail
echo.
pause
exit /b 1
