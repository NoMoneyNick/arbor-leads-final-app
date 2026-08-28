@echo off
REM One-click deploy helper for TreeKey / Vector Data Labs.
REM Double-click this file (or run it from a terminal) any time you want to
REM send local changes to GitHub, which triggers Render to auto-deploy.
REM Safe to run even if nothing has changed - it'll just say so and stop.

cd /d "%~dp0"

echo ============================================
echo  TreeKey Deploy Helper
echo ============================================
echo.

echo [1/3] Staging local changes...
git add .

set msg=
set /p msg="Commit message (press Enter to use a default): "
if "%msg%"=="" set msg=Update %date% %time%

git commit -m "%msg%"
echo.

echo [2/3] Pulling any changes already on GitHub first (e.g. from Antigravity)...
git pull
if errorlevel 1 (
    echo.
    echo *** git pull reported a problem above - likely a merge conflict. ***
    echo *** Do NOT push yet. Copy the message above and ask Claude for help. ***
    pause
    exit /b 1
)
echo.

echo [3/3] Pushing to GitHub...
git push
if errorlevel 1 (
    echo.
    echo *** git push failed - see the message above. ***
    echo *** Copy it and ask Claude for help rather than retrying blindly. ***
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Done. Render will auto-deploy from this push.
echo ============================================
pause
