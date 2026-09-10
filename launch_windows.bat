@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo        3D Slicer Bounding Box Navigator Launcher
echo =======================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "MODULE_DIR=%SCRIPT_DIR%BoundingBoxNavigator"

:: ------------------------------------------------------------------------
:: 1. Check if files were extracted or if running directly inside a ZIP
:: ------------------------------------------------------------------------
if not exist "%MODULE_DIR%\BoundingBoxNavigator.py" (
    echo [ERROR] Could not find BoundingBoxNavigator files at:
    echo "%MODULE_DIR%"
    echo.
    echo ------------------------------------------------------------------------
    echo DID YOU OPEN THIS FILE DIRECTLY FROM INSIDE A .ZIP FILE?
    echo Windows opens ZIP files as preview folders, but does not extract
    echo the subfolders until you explicitly extract them.
    echo.
    echo HOW TO FIX:
    echo   1. Close this window.
    echo   2. Find your downloaded "3d-slicer-annotation-tool-main.zip".
    echo   3. Right-click the .zip file and select "Extract All..." (Alles uitpakken).
    echo   4. Choose an extraction folder (e.g. in Documents or Desktop) and click Extract.
    echo   5. Open the extracted folder and double-click "launch_windows.bat" again.
    echo ------------------------------------------------------------------------
    echo.
    pause
    exit /b 1
)

:: ------------------------------------------------------------------------
:: 2. Find Slicer.exe
:: ------------------------------------------------------------------------

:: Check if Slicer is already in PATH
where Slicer.exe >nul 2>nul
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where Slicer.exe') do (
        set "SLICER_EXE=%%i"
        goto :FOUND
    )
)

:: Use PowerShell to search Start Menu shortcuts, standard dirs, and registry
set "PS_FIND_CMD=$ErrorActionPreference='SilentlyContinue'; $found=''; $sm=@(\"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\", \"$env:ProgramData\Microsoft\Windows\Start Menu\Programs\"); $wsh=New-Object -ComObject WScript.Shell; foreach($d in $sm){ if(Test-Path $d){ foreach($l in (Get-ChildItem -Path $d -Filter '*Slicer*.lnk' -Recurse)){ $t=$wsh.CreateShortcut($l.FullName).TargetPath; if($t -like '*Slicer.exe' -and (Test-Path $t)){ $found=$t; break } } } if($found){ break } }; if(-not $found){ $dirs=@(\"$env:LOCALAPPDATA\slicer.org\", \"$env:LOCALAPPDATA\NA-MIC\", \"$env:LOCALAPPDATA\Programs\", \"$env:ProgramFiles\", \"${env:ProgramFiles(x86)}\"); foreach($p in $dirs){ if(Test-Path $p){ $e=Get-ChildItem -Path $p -Filter 'Slicer.exe' -Recurse -Depth 3 | Select-Object -First 1; if($e){ $found=$e.FullName; break } } } }; if($found){ Write-Output $found }"

for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "%PS_FIND_CMD%"`) do (
    if exist "%%p" (
        set "SLICER_EXE=%%p"
        goto :FOUND
    )
)

:: ------------------------------------------------------------------------
:: 3. If auto-detection did not find Slicer, prompt the user
:: ------------------------------------------------------------------------
echo Could not automatically detect Slicer.exe.
echo.
echo TIP: You can drag and drop either:
echo   - Slicer.exe
echo   - The 3D Slicer Start Menu shortcut (e.g. from C:\Users\...\Start Menu\Programs\3D Slicer...)
echo   - The 3D Slicer installation folder
echo.
set /p "USER_INPUT=Please enter or drag-and-drop Slicer path here: "
set "USER_INPUT=%USER_INPUT:"=%"

if "%USER_INPUT%"=="" (
    echo [ERROR] No path provided.
    pause
    exit /b 1
)

:: Resolve the user input via PowerShell (handles .lnk shortcuts, folders, or direct .exe)
set "PS_RESOLVE_CMD=$ErrorActionPreference='SilentlyContinue'; $in='%USER_INPUT%'; if(Test-Path $in){ if($in.EndsWith('.lnk')){ $wsh=New-Object -ComObject WScript.Shell; $t=$wsh.CreateShortcut($in).TargetPath; if(Test-Path $t){ Write-Output $t; exit } } elseif(Test-Path -PathType Container $in){ $e=Get-ChildItem -Path $in -Filter 'Slicer.exe' -Recurse -Depth 3 | Select-Object -First 1; if($e){ Write-Output $e.FullName; exit }; $l=Get-ChildItem -Path $in -Filter '*Slicer*.lnk' -Recurse -Depth 3 | Select-Object -First 1; if($l){ $wsh=New-Object -ComObject WScript.Shell; $t=$wsh.CreateShortcut($l.FullName).TargetPath; if(Test-Path $t){ Write-Output $t; exit } } } elseif($in.EndsWith('Slicer.exe')){ Write-Output $in; exit } }"

for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "%PS_RESOLVE_CMD%"`) do (
    if exist "%%p" (
        set "SLICER_EXE=%%p"
        goto :FOUND
    )
)

if not exist "%SLICER_EXE%" (
    echo [ERROR] Could not find Slicer.exe from input: "%USER_INPUT%"
    echo.
    pause
    exit /b 1
)

:FOUND
echo.
echo [OK] Using Slicer: "%SLICER_EXE%"
echo [OK] Module path:  "%MODULE_DIR%"
echo.
echo Starting 3D Slicer with Bounding Box Navigator...

start "" "%SLICER_EXE%" --additional-module-paths "%MODULE_DIR%" --python-code "slicer.util.selectModule('BoundingBoxNavigator')"

endlocal
