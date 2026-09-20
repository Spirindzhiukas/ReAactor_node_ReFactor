@echo off
setlocal EnableExtensions
rem ====================================================================
rem  ANTs crash-offset resolver - names the function at MODULE+0xOFFSET
rem
rem  Usage (double-click works too):
rem    resolve_offsets.bat 0x27799              - offsets in the NR dll below
rem    resolve_offsets.bat KERNEL32 0x27799     - offsets in System32 KERNEL32.DLL
rem  With no arguments it asks for the offset interactively.
rem  Output is shown AND copied to the clipboard automatically.
rem
rem  ============== THE ONLY BLOCK YOU MAY EDIT ==============
set "PY=C:\ComfyUI_PORTABLE\python_embeded\python.exe"
set "SCRIPT=C:\ComfyUI_PORTABLE\resolve_crash_offset.py"
set "DLL=C:\ComfyUI_PORTABLE\ComfyUI\models\DLSS\NR\nvngx_dlssnr_RenoDX_4000_series_friendly.dll"
rem  ============== END OF THE BLOCK YOU MAY EDIT ==============
rem  If the embedded python is missing, a system python is used instead.

if not exist "%PY%" set "PY=python"

set "OFFS="
:args
if "%~1"=="" goto have_args
if /i "%~1"=="KERNEL32" set "DLL=C:\Windows\System32\KERNEL32.DLL"
if /i "%~1"=="KERNEL32.DLL" set "DLL=C:\Windows\System32\KERNEL32.DLL"
if /i not "%~1"=="KERNEL32" if /i not "%~1"=="KERNEL32.DLL" call set "OFFS=%%OFFS%% %~1"
shift
goto args
:have_args
if not defined OFFS set /p "OFFS=Type ONLY the offset, example 0x27799, and press ENTER: "
if /i "%OFFS:~0,8%"=="KERNEL32" set "DLL=C:\Windows\System32\KERNEL32.DLL"
if /i "%OFFS:~0,8%"=="KERNEL32" call set "OFFS=%%OFFS:~9%%"
if not defined OFFS goto usage
if not exist "%SCRIPT%" echo [X] resolver missing: "%SCRIPT%" - copy resolve_crash_offset.py there, or fix the SCRIPT line in this bat & goto fail
if not exist "%DLL%" echo [X] dll missing: "%DLL%" - fix the DLL line in this bat & goto fail

"%PY%" "%SCRIPT%" "%DLL%" %OFFS% > "%TEMP%\ants_resolve.txt" 2>&1
type "%TEMP%\ants_resolve.txt"
echo.
echo [OK] result also copied to the clipboard - paste it all back to the chat.
clip < "%TEMP%\ants_resolve.txt"
pause
exit /b 0
:usage
echo Usage: resolve_offsets.bat 0x27799 [more offsets]
echo    or: resolve_offsets.bat KERNEL32 0x27799
:fail
pause
exit /b 1
