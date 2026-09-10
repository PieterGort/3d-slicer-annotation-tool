@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo        3D Slicer Bounding Box Navigator Launcher
echo =======================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "MODULE_DIR=%SCRIPT_DIR%BoundingBoxNavigator"

if not exist "%MODULE_DIR%\BoundingBoxNavigator.py" (
    echo [ERROR] Could not find BoundingBoxNavigator at:
    echo "%MODULE_DIR%"
    echo.
    pause
    exit /b 1
)

:: 1. Check if Slicer is already in PATH
where Slicer.exe >nul 2>nul
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where Slicer.exe') do (
        set "SLICER_EXE=%%i"
        goto :FOUND
    )
)

:: 2. Search common default installation directories on Windows
if exist "%LOCALAPPDATA%\slicer.org\" (
    for /f "delims=" %%d in ('dir /b /ad /o-n "%LOCALAPPDATA%\slicer.org\Slicer*" 2^>nul') do (
        if exist "%LOCALAPPDATA%\slicer.org\%%d\Slicer.exe" (
            set "SLICER_EXE=%LOCALAPPDATA%\slicer.org\%%d\Slicer.exe"
            goto :FOUND
        )
    )
)

if exist "%ProgramFiles%\Slicer*" (
    for /f "delims=" %%d in ('dir /b /ad /o-n "%ProgramFiles%\Slicer*" 2^>nul') do (
        if exist "%ProgramFiles%\%%d\Slicer.exe" (
            set "SLICER_EXE=%ProgramFiles%\%%d\Slicer.exe"
            goto :FOUND
        )
    )
)

if exist "%ProgramFiles(x86)%\Slicer*" (
    for /f "delims=" %%d in ('dir /b /ad /o-n "%ProgramFiles(x86)%\Slicer*" 2^>nul') do (
        if exist "%ProgramFiles(x86)%\%%d\Slicer.exe" (
            set "SLICER_EXE=%ProgramFiles(x86)%\%%d\Slicer.exe"
            goto :FOUND
        )
    )
)

:: 3. Prompt user if not found automatically
echo Could not automatically detect Slicer.exe in standard directories.
echo.
set /p "SLICER_EXE=Please enter or drag-and-drop the full path to Slicer.exe: "
set "SLICER_EXE=%SLICER_EXE:"=%"

if not exist "%SLICER_EXE%" (
    echo [ERROR] Slicer.exe not found at: "%SLICER_EXE%"
    echo.
    pause
    exit /b 1
)

:FOUND
echo [OK] Using Slicer: "%SLICER_EXE%"
echo [OK] Module path:  "%MODULE_DIR%"
echo.
echo Starting 3D Slicer...

start "" "%SLICER_EXE%" --additional-module-paths "%MODULE_DIR%" --python-code "slicer.util.selectModule('BoundingBoxNavigator')"

endlocal
