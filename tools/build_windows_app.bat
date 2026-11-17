@echo off
rem Builds dist\LegalCMS.exe, a Windows app that runs without Python installed.
rem Double-click this file (it lives in the "tools" folder of the project).
cd /d "%~dp0.."

echo Installing the build tools (needs internet)...
py -m pip install pyinstaller -r requirements.txt
if errorlevel 1 goto failed

echo.
echo Combining the app into one file...
py tools\make_single_file.py
if errorlevel 1 goto failed

echo.
echo Building the app (this takes a few minutes)...
py -m PyInstaller --onefile --clean --noconfirm --name LegalCMS --distpath dist --workpath build dist\legal_cms.py
if errorlevel 1 goto failed

echo.
echo Done. Your app is here:  %cd%\dist\LegalCMS.exe
pause
exit /b 0

:failed
echo.
echo The build did not finish. Take a screenshot of the messages above.
pause
exit /b 1
