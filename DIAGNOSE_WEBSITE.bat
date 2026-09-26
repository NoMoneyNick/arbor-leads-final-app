@echo off
REM ============================================================
REM  DIAGNOSE WEBSITE - just double-click this file. Nothing to type.
REM  This does NOT change or send anything anywhere. It only looks
REM  and prints what it finds. Screenshot the whole window and send
REM  it to Claude.
REM ============================================================

cd /d "%~dp0"

echo.
echo   ==================== FOLDER ====================
cd
echo.
echo   ==================== REMOTE (where pushes go) ====================
git remote -v
echo.
echo   ==================== CURRENT BRANCH ====================
git branch -vv
echo.
echo   ==================== LOCAL STATUS (uncommitted changes) ====================
git status
echo.
echo   ==================== LAST 5 COMMITS (local) ====================
git log -5 --oneline
echo.
echo   ==================== LAST 5 COMMITS (on GitHub, as last seen) ====================
git log -5 --oneline origin/main 2>nul
git log -5 --oneline origin/master 2>nul
echo.
echo   ==================== DONE - screenshot this whole window ====================
pause
