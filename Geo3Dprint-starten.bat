@echo off
setlocal

title Geo3Dprint - Normaler Modus
color 0A

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
set "RUNTIME_DIR=%ROOT%.runtime"
set "NUGET_EXE=%RUNTIME_DIR%\nuget.exe"
set "PORTABLE_PY=%RUNTIME_DIR%\python\tools\python.exe"
set "PYTHON_VERSION=3.12.10"
set "REQ=%ROOT%requirements.txt"
set "APP=%ROOT%programm.py"

pushd "%ROOT%" >nul 2>nul
if errorlevel 1 (
    echo Geo3Dprint konnte den Projektordner nicht oeffnen.
    echo Pfad: %ROOT%
    goto end
)

cls
echo ===============================================
echo   Geo3Dprint
echo   Normaler Modus
echo ===============================================
echo.

if not exist "%VENV_PY%" (
    echo Richte Geo3Dprint fuer diesen Ordner ein...

    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%ROOT%.venv"
    )

    if not exist "%VENV_PY%" (
        where python >nul 2>nul
        if not errorlevel 1 (
            python -m venv "%ROOT%.venv"
        )
    )

    if not exist "%VENV_PY%" (
        if not exist "%PORTABLE_PY%" (
            echo Kein nutzbares Python gefunden. Lade lokale Python-Laufzeit herunter...
            if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"

            "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://aka.ms/nugetclidl' -OutFile $env:NUGET_EXE; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
            if errorlevel 1 (
                echo NuGet konnte nicht heruntergeladen werden.
                echo Bitte Internetverbindung pruefen und erneut starten.
                goto end
            )

            "%NUGET_EXE%" install python -Version %PYTHON_VERSION% -ExcludeVersion -OutputDirectory "%RUNTIME_DIR%" -NonInteractive
            if errorlevel 1 (
                echo Die lokale Python-Laufzeit konnte nicht heruntergeladen werden.
                echo Bitte Internetverbindung pruefen und erneut starten.
                goto end
            )
        )

        if not exist "%PORTABLE_PY%" (
            echo Die lokale Python-Laufzeit wurde nicht gefunden.
            goto end
        )

        "%PORTABLE_PY%" -m venv "%ROOT%.venv"
        if errorlevel 1 (
            echo Die lokale Python-Umgebung konnte nicht erstellt werden.
            goto end
        )
    )
)

if exist "%REQ%" (
    echo Pruefe Python-Abhaengigkeiten...
    "%VENV_PY%" -m pip install --disable-pip-version-check -r "%REQ%"
    if errorlevel 1 (
        echo Die Python-Abhaengigkeiten konnten nicht installiert werden.
        goto end
    )
    echo.
)

echo Starte Geo3Dprint im normalen Modus...
echo.
"%VENV_PY%" "%APP%"
set "APP_EXIT=%ERRORLEVEL%"

echo.
if not "%APP_EXIT%"=="0" (
    echo Geo3Dprint wurde mit Fehlercode %APP_EXIT% beendet.
) else (
    echo Geo3Dprint wurde beendet.
)

:end
echo.
echo Dieses Fenster kann geschlossen werden.
pause >nul
popd >nul 2>nul
endlocal
