@echo off
rem Atelier3D: double-click to install (first run) and open the studio.
rem   Atelier3D.bat --demo    try the studio without a GPU or model downloads
rem   Atelier3D.bat --port 7880
setlocal
cd /d "%~dp0"
title Atelier3D

set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"

if not exist ".venv\Scripts\python.exe" (
  echo [Atelier3D] creating the studio environment...
  if defined UV (
    "%UV%" venv --python 3.12 .venv || goto :fail
  ) else (
    py -3.12 -m venv .venv 2>nul || python -m venv .venv || goto :nopython
  )
)
if not exist ".venv\.deps-ok" (
  if defined UV (
    "%UV%" pip install --python .venv\Scripts\python.exe -r requirements.txt || goto :fail
  ) else (
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
  )
  echo ok> .venv\.deps-ok
)

if /i not "%~1"=="--demo" (
  .venv\Scripts\python.exe scripts\setup_engine.py || goto :fail
)

.venv\Scripts\python.exe -m atelier --open %*
if errorlevel 1 goto :fail
exit /b 0

:nopython
echo [Atelier3D] Python 3.10+ was not found. Install Python 3.12 from python.org or uv from astral.sh, then run this again.
pause
exit /b 1

:fail
echo [Atelier3D] something went wrong; the messages above say what.
pause
exit /b 1
