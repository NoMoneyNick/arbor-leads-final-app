@echo off
REM ============================================================
REM  ONE-TIME FIX v2 - just double-click this once, then go back
REM  to using UPDATE_WEBSITE.bat as normal.
REM
REM  What happened: a large helper program called tailwindcss.exe
REM  (about 107 MB) got saved into an earlier "save point" of this
REM  website's history. GitHub refuses anything over 100 MB, so
REM  every attempt to send your changes has been failing -- and
REM  the first version of this fix wasn't enough, because just
REM  removing the file from FUTURE save points doesn't remove it
REM  from the one it's already stuck inside.
REM
REM  What this version does instead: since NONE of your recent
REM  changes have successfully reached GitHub yet (every attempt
REM  has failed with the same error), it's completely safe to
REM  rewind to the last point that DID reach GitHub successfully,
REM  and re-save all your current files fresh from there -- this
REM  time without the oversized file. Nothing on your computer is
REM  touched or deleted; this only rewrites the "save point"
REM  bookkeeping, not your actual files.
REM
REM  This window shows the real output the whole way through, so
REM  if anything still goes wrong, a screenshot of this window
REM  will show Claude exactly what.
REM ============================================================

cd /d "%~dp0"

echo.
echo   Step 1: Checking what GitHub actually has right now...
echo.
git fetch origin

echo.
echo   Step 2: Rewinding to that point (your files on this computer
echo   are NOT changed by this -- only the save-point bookkeeping)...
echo.
git reset --soft origin/main

echo.
echo   Step 3: Making sure the oversized file is excluded this time...
echo.
git rm --cached tailwindcss.exe

echo.
echo   Step 4: Saving everything as one fresh, clean update...
echo.
git add .
git commit -m "Website update (large tool binary excluded)"

echo.
echo   Step 5: Sending it to the website...
echo.
git push

echo.
echo ============================================================
echo   If everything above looks OK (no lines containing the word
echo   "error" or "rejected"), you're done! From now on just use
echo   UPDATE_WEBSITE.bat like normal, and you can delete this file.
echo.
echo   If you DO see "error" or "rejected" anywhere above, take a
echo   screenshot of this whole window and send it to Claude.
echo ============================================================
pause
