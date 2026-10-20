"""
page_theme.py -- applies the shared TreeKey page theme to the "remaining pages".

WHAT IT DOES
  For the pages listed in V2_PATHS / V2_PREFIXES it takes the HTML a route has
  already produced and
    1. adds class "tk-v2" to <body>        (turns on static/treekey-pages.css)
    2. links /static/treekey-pages.css      (before </head>)
    3. swaps the old header/footer for the new ones (passed in by main.py, so
       login state, links and wording stay owned by main.py)
    4. wraps the page body in <div class="tk-content">

  Routes, forms, cookies, validation and business wording are NOT touched: the
  route code runs exactly as before and only its finished HTML is re-wrapped.

WHAT IT NEVER TOUCHES
  The approved homepage "/" and "/marketplace", every JSON/API/redirect/file
  response, printed documents (/generate-letter, /generate-street-flyer,
  /generate-storm-quote) and any admin page not listed below.

TURN IT OFF
  Set the environment variable TREEKEY_PAGE_THEME=off and every page renders
  exactly as it did before this change.

TO ADD OR REMOVE A PAGE: edit V2_PATHS / V2_PREFIXES below.
"""
import os
import re
from typing import Callable

# Exact paths that get the theme (GET and POST responses alike).
V2_PATHS = frozenset({
    # public
    "/pricing", "/faq", "/privacy-policy", "/terms-of-service", "/suggestions", "/partner-offer",
    "/api/submit-suggestion", "/api/submit-partner-offer",
    "/unsubscribe", "/unsubscribe-teaser",
    # sign-in / account
    "/login", "/api/request-magic-link", "/free-account", "/api/signup", "/api/signup/complete",
    "/welcome", "/account", "/account/close", "/settings", "/dashboard", "/free-dashboard", "/my-leads",
    # purchase and letter setup
    "/letter-settings", "/letter-settings/preview", "/payment/success",
    # tools
    "/chip-drop", "/register-drop-spot", "/storm-radar", "/ledger", "/boost-review", "/quote-estimator",
    # internal pages that have an approved design
    "/admin", "/status", "/admin/lead-volume-report", "/admin/lead-value-report", "/admin/reclassify-audit",
    "/admin/vertical-audit", "/admin/letter-dispatches", "/admin/account-requests",
})

# Path prefixes (everything below them).
V2_PREFIXES = ("/checkout/", "/marketplace/lead/", "/tree-surgeon/")

STYLESHEET_HREF = "/static/treekey-pages.css?v=2"   # bump v= after editing the CSS so browsers reload it

_BODY_RE = re.compile(r"(<body\b)([^>]*)(>)(.*)(</body\s*>)", re.I | re.S)
_NAV_RE = re.compile(r'<nav class="tk-nav"[\s\S]*?</nav>', re.I)
_FOOTER_RE = re.compile(r'<footer class="tk-footer"[\s\S]*?</footer>', re.I)
_CLASS_RE = re.compile(r'class\s*=\s*(["\'])(.*?)\1', re.I | re.S)


def enabled() -> bool:
    return os.getenv("TREEKEY_PAGE_THEME", "on").strip().lower() not in ("off", "0", "false", "no")


def applies_to(path: str, status_code: int = 200) -> bool:
    """True if a response for this path should get the theme."""
    if not enabled():
        return False
    if path in V2_PATHS or path.startswith(V2_PREFIXES):
        return True
    if path == "/marketplace" and os.getenv("TREEKEY_MARKET_V2", "on").strip().lower() not in ("off", "0", "false", "no"):
        return True   # 20 Oct 2026: marketplace gets the new header, footer and theme (TREEKEY_MARKET_V2=off restores the old look)
    # Branded "page not found" for any non-API, non-admin address.
    return status_code == 404 and not path.startswith(("/api/", "/admin"))


def _add_body_class(attrs: str) -> str:
    m = _CLASS_RE.search(attrs)
    if m:
        return attrs[:m.start()] + f'class={m.group(1)}{m.group(2)} tk-v2{m.group(1)}' + attrs[m.end():]
    return attrs + ' class="tk-v2"'


def wrap(html_text: str, header_html: str, footer_html: str, strip_css: tuple = ()) -> str:
    """Return html_text re-wrapped in the shared header/footer + theme.
    `strip_css` are the exact old shared-nav/footer <style> blocks to remove.
    Returns the input unchanged if it is not a full page."""
    m = _BODY_RE.search(html_text)
    if not m or "tk-v2" in m.group(2):
        return html_text
    inner = m.group(4)
    for css in strip_css:
        inner = inner.replace(css, "")
    inner = _NAV_RE.sub("", inner, count=1)
    inner = _FOOTER_RE.sub("", inner, count=1)
    body = (f"{m.group(1)}{_add_body_class(m.group(2))}{m.group(3)}\n{header_html}\n"
            f'<div class="tk-content" id="main">{inner}</div>\n{footer_html}\n{m.group(5)}')
    out = html_text[:m.start()] + body + html_text[m.end():]
    link = f'<link rel="stylesheet" href="{STYLESHEET_HREF}">'
    if re.search(r"</head\s*>", out, re.I):
        out = re.sub(r"</head\s*>", link + "\n</head>", out, count=1, flags=re.I)
    else:
        out = out.replace("<body", link + "\n<body", 1)
    return out
