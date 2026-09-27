@echo off
REM ============================================================
REM  RUN INTELLIPRINT AUTH CHECK - just double-click this file.
REM  Nothing to type.
REM
REM  What it does, in plain English:
REM  1. Sends ONE read-only request to Intelliprint's real API (the
REM     documented "list my print jobs" endpoint) using your API key
REM     from your local .env file.
REM  2. This CANNOT create a print job, charge anything, or post
REM     anything, ever -- it only asks Intelliprint "is this key
REM     allowed to see my account's jobs at all?"
REM  3. Prints back whether that succeeded or failed, and why --
REM     never your API key.
REM
REM  Why: your last test run of RUN_INTELLIPRINT_TEST.bat got a
REM  "403 Forbidden" error. This check narrows down whether that's a
REM  problem with the key/account itself, or something specific to
REM  submitting print jobs -- before trying that again.
REM
REM  Before running this for the first time: put your Intelliprint
REM  API key in a file called .env in this same folder -- see the
REM  INTELLIPRINT_API_KEY section near the bottom of
REM  .env.example.letter-fulfilment for exactly where to get it and
REM  what line to add. Do not paste the key anywhere else, including
REM  to Claude in chat.
REM
REM  When it's done, screenshot the whole window and send it over --
REM  it never prints your API key, so it's safe to share, including
REM  with Intelliprint support if that turns out to be the next step.
REM ============================================================

cd /d "%~dp0"

python scripts\intelliprint_auth_check.py
if errorlevel 1 (
    echo.
    echo   ============================================================
    echo   See the message above for what to do next.
    echo   If it says "python is not recognised", Python isn't
    echo   installed/on PATH on this machine -- tell Claude that and
    echo   we'll sort out another way to run this.
    echo   ============================================================
)

echo.
pause
