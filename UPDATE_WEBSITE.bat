@echo off
REM ============================================================
REM  UPDATE WEBSITE - just double-click this file. Nothing to type.
REM
REM  What it does, in plain English:
REM  1. Gathers up whatever Claude has changed in this folder
REM  2. Sends it to GitHub
REM  3. GitHub tells Render to rebuild the live website automatically
REM
REM  You do not need to understand git, commits, or any of the words
REM  that scroll past below. Just wait for the final message.
REM ============================================================

cd /d "%~dp0"
setlocal enabledelayedexpansion

echo.
echo   Checking for changes to send...
echo.

git add . >nul 2>&1
git commit -m "Website update %date% %time%" >nul 2>&1

echo   Getting the latest version from GitHub first...
echo.
git pull >nul 2>&1
if errorlevel 1 (
    echo   ============================================================
    echo   SOMETHING NEEDS ATTENTION - please do NOT close this window.
    echo   Take a screenshot of this whole window and send it to Claude.
    echo   ============================================================
    pause
    exit /b 1
)

echo   Sending your changes to the website...
echo.
git push >nul 2>&1
if errorlevel 1 (
    echo   ============================================================
    echo   SOMETHING NEEDS ATTENTION - please do NOT close this window.
    echo   Take a screenshot of this whole window and send it to Claude.
    echo   ============================================================
    pause
    exit /b 1
)

echo   ============================================================
echo   DONE. Your website will update in a minute or two.
echo   You can close this window now.
echo   ============================================================
pause
