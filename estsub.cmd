@echo off
rem Käivitab estsub.py Exploreri paremkliki menüüst. %1 = kaust või videofail.
chcp 65001 >nul
title Eesti subtiitrid
python "%~dp0estsub.py" %*
echo.
if errorlevel 1 (echo VIGA - vaata teadet ylal.) else (echo Valmis. Plexis tee filmil "Refresh Metadata".)
pause
