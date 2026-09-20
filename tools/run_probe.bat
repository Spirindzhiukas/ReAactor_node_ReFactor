@echo off
setlocal
REM ==================================================================
REM  [ANTs] probe runner - analyzes the PROVEN C++ NGX host binaries
REM  (Merserk Visual Enhancer v10) that sit in the same folder.
REM  READ-ONLY: nothing in the Visual Enhancer folder is modified.
REM
REM  WHERE THIS FILE MUST LIVE:
REM    the Visual.Enhancer.v10.0 root, next to probe_neuroframe_host.py
REM    (the folder that contains runtime\dlssnr\nvngx_dlssnr.dll).
REM    Double-click it, or run it from cmd - same result.
REM
REM  ==== [ANTs] EDIT THIS BLOCK ONLY IF AUTO-DETECT PICKS A BAD PYTHON ====
REM  Preferred: ComfyUI's own embedded python. Uncomment ONE line and fix
REM  the path if needed (keep the "rem " lowercase at line start):
rem set "COMFYUI_PYTHON=D:\AI_STUFF\ComfyUI_windows_portable\python_embeded\python.exe"
REM  Or, if you keep a COMFYUI_PORTABLE environment variable on your system,
REM  the bat already tries %COMFYUI_PORTABLE%\python_embeded\python.exe.
REM  Otherwise it falls back to the system python (py launcher, then PATH).
REM  ======================================================================

REM --- folder of this bat = the VE root to probe (strip trailing backslash)
set "VE_ROOT=%~dp0"
if "%VE_ROOT:~-1%"=="\" set "VE_ROOT=%VE_ROOT:~0,-1%"
cd /d "%VE_ROOT%"

if not exist "probe_neuroframe_host.py" (
    echo [ANTs] probe_neuroframe_host.py is NOT next to this bat.
    echo        Copy BOTH files into the Visual.Enhancer.v10.0 root
    echo        the folder containing runtime\dlssnr - and run again.
    echo.
    pause
    exit /b 1
)

set "PYEXE="
set "PYARGS="
REM 1) the explicitly edited path at the top of this file
if defined COMFYUI_PYTHON if exist "%COMFYUI_PYTHON%" set "PYEXE=%COMFYUI_PYTHON%"
REM 2) ComfyUI portable root advertised via environment variable
if not defined PYEXE if defined COMFYUI_PORTABLE if exist "%COMFYUI_PORTABLE%\python_embeded\python.exe" set "PYEXE=%COMFYUI_PORTABLE%\python_embeded\python.exe"
REM 3) the bat itself sits inside a ComfyUI portable root
if not defined PYEXE if exist "%VE_ROOT%\python_embeded\python.exe" set "PYEXE=%VE_ROOT%\python_embeded\python.exe"
REM 4) ComfyUI portable next door to this folder
if not defined PYEXE if exist "%VE_ROOT%\..\ComfyUI_windows_portable\python_embeded\python.exe" set "PYEXE=%VE_ROOT%\..\ComfyUI_windows_portable\python_embeded\python.exe"
REM 5) system python via the py launcher (python.org installs)
if not defined PYEXE for /f "delims=" %%P in ('where py 2^>nul') do (
    if not defined PYEXE (
        set "PYEXE=%%P"
        set "PYARGS=-3"
    )
)
REM 6) system python on PATH - skip the Microsoft Store stub in WindowsApps
if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul ^| find /i /v "WindowsApps"') do (
    if not defined PYEXE set "PYEXE=%%P"
)

if not defined PYEXE (
    echo [ANTs] No usable Python found.
    echo        Edit this bat: uncomment the COMFYUI_PYTHON line at the top
    echo        and point it at your ComfyUI portable python_embeded\python.exe
    echo.
    pause
    exit /b 1
)

REM --- sanity: the chosen python must actually run (catches store stubs)
"%PYEXE%" %PYARGS% -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [ANTs] Python at "%PYEXE%" did not respond.
    echo        Edit this bat and set COMFYUI_PYTHON to a working python.exe.
    echo.
    pause
    exit /b 1
)

echo [ANTs] python : %PYEXE% %PYARGS%
echo [ANTs] target : %VE_ROOT%
echo [ANTs] reading PE tables + strings, the 158 MB runtime takes a moment...
echo.
"%PYEXE%" %PYARGS% "probe_neuroframe_host.py" "%VE_ROOT%"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
    echo [ANTs] The probe reported a problem - exit code %RC%.
    echo        Read its message above and paste the console output
    echo        into the chat so it can be fixed.
    echo.
    pause
    exit /b %RC%
)
if exist "probe_neuroframe_host_report.txt" (
    type "probe_neuroframe_host_report.txt" | clip
    echo [ANTs] DONE. The FULL report is already on your CLIPBOARD -
    echo        just switch to the chat and press Ctrl+V.
    echo [ANTs] also saved as: %VE_ROOT%\probe_neuroframe_host_report.txt
) else (
    echo [ANTs] No report file was produced - paste the console output
    echo        above into the chat so it can be debugged.
)
echo.
pause
exit /b %RC%
