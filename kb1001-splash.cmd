@echo off
setlocal
cd /d "%~dp0"

set "PYRUN="
where py >nul 2>nul
if %errorlevel%==0 set "PYRUN=py -3"
if not defined PYRUN (
    where python >nul 2>nul
    if %errorlevel%==0 set "PYRUN=python"
)

if not defined PYRUN (
    echo Python 3 was not found. Install Python 3 and retry.
    exit /b 9009
)

rem No arguments: normal menu.
if "%~1"=="" (
    %PYRUN% "%~dp0kb1001_splash_updater.py"
    exit /b %errorlevel%
)

rem If the first argument is a real file, this is image-input mode.
rem One image = same image in both stock splash slots.
rem Two images = first -> bootlogo.bmp, second -> bootlogo-go.bmp.
if exist "%~1" (
    if "%~2"=="" (
        %PYRUN% "%~dp0kb1001_splash_updater.py" "%~1"
        exit /b %errorlevel%
    )

    if exist "%~2" (
        if not "%~3"=="" (
            echo ERROR: Drag/drop or supply only one or two images.
            exit /b 2
        )
        %PYRUN% "%~dp0kb1001_splash_updater.py" "%~1" "%~2"
        exit /b %errorlevel%
    )
)

rem Otherwise pass through normal commands such as status/restore/update.
%PYRUN% "%~dp0kb1001_splash_updater.py" %*
exit /b %errorlevel%
