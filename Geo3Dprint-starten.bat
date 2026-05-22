@echo off
setlocal

title Geo3Dprint - Normaler Modus
color 0A

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
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
    echo Richte die lokale Python-Umgebung ein...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%ROOT%.venv"
    ) else (
        where python >nul 2>nul
        if errorlevel 1 (
            echo Python wurde nicht gefunden.
            echo Bitte Python 3 installieren und danach erneut starten.
            goto end
        )
        python -m venv "%ROOT%.venv"
    )

    if errorlevel 1 (
        echo Die lokale Python-Umgebung konnte nicht erstellt werden.
        goto end
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
