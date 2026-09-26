@echo off
REM ============================================================
REM  RUN INTELLIPRINT TEST - just double-click this file. Nothing
REM  to type.
REM
REM  What it does, in plain English:
REM  1. Sends ONE test-mode letter submission to Intelliprint's real
REM     API, using your API key from your local .env file.
REM  2. Test mode is locked ON inside the script itself -- this can
REM     NEVER cost money or actually post a letter, no matter what.
REM  3. Prints back what Intelliprint said, so we can confirm the
REM     integration actually works before going any further.
REM
REM  Before running this for the first time: put your Intelliprint
REM  API key in a file called .env in this same folder -- see the
REM  INTELLIPRINT_API_KEY section near the bottom of
REM  .env.example.letter-fulfilment for exactly where to get it and
REM  what line to add. Do not paste the key anywhere else, including
REM  to Claude in chat.
REM
REM  When it's done, screenshot the whole window (like
REM  UPDATE_WEBSITE.bat asks) and send it over -- it never prints
REM  your API key, so it's safe to share.
REM ============================================================

cd /d "%~dp0"

python scripts\intelliprint_test_send.py
if errorlevel 1 (
    echo.
    echo   ============================================================
    echo   Something needs attention -- see the message above.
    echo   If it says "python is not recognised", Python isn't
    echo   installed/on PATH on this machine -- tell Claude that and
    echo   we'll sort out another way to run this.
    echo   ============================================================
)

echo.
pause
