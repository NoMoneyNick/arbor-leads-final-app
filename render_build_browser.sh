#!/usr/bin/env bash
# Run during the Render BUILD, after "pip install -r requirements.txt".
# Installs the Chromium that the installed Playwright package needs into a folder inside the project
# (so it is still there when the service starts), then renders a real test PDF. If that render fails,
# this script fails and Render keeps the previous working version live instead of deploying a broken one.
set -euo pipefail
cd "$(dirname "$0")"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
python -m playwright install chromium
python scripts/pdf_render_check.py
