"""
brand.py -- the ONE place for TreeKey brand colours, wording and icon/share-link metadata.

WHO USES IT
  * email_layout.py      (emails: colours, crest URL, footer wording)
  * main.py              (flyer + storm quote print styles; head icons via apply_head_branding)
  * letter_content.py    (printed letter colours; it keeps its own copy of the print CSS)
  * tools/build_brand_assets.py (regenerates the icon / share images from these colours)
  The website's own stylesheet (static/treekey-pages.css section 1) keeps its own CSS variables (the
  approved web palette, slightly different from the print/email values here). Change web colours there.
  tools/brand_check.py checks icon sizes, manifest, share image and page tags.

TO CHANGE THE LOOK LATER
  Colours / wording   -> edit COLORS / the wording constants below.
  Icons, share image  -> replace assets/brand/treekey-crest-master.png, run tools/build_brand_assets.py,
                         then bump ASSET_VERSION (phones and browsers cache icons hard).
  Turn the icon/meta rewrite off -> set environment variable TREEKEY_BRAND_HEAD=off.
"""
import os
import re

# ------------------------------------------------------------------ wording
NAME = "TreeKey"
TAGLINE = "LOCAL WORK. A CLEARER VIEW."                        # shown in email headers, share image, launch screen
OG_HEADLINE_LINES = ("Find your next", "tree-work opportunity.")  # share-image headline (approved design)
OG_TITLE = "TreeKey | Local tree-work opportunities"
OG_DESCRIPTION = "Discover potential tree work in the areas you cover."
FOOTER_TAGLINE = "Built around the way tree contractors work."    # email footer line (approved email design)
OPERATOR_LINE = "TreeKey is operated by Vector Data Labs, trading as TreeKey."   # same wording as the letter's reverse page
CANONICAL_HOST = "treekey.co.uk"

# Serif for headings / wordmark. Georgia is not installed on most servers, so the printer's browser falls back
# to the next name in the list; the layout is sized to stay on two pages with any of them.
SERIF = "Georgia, 'Times New Roman', 'Liberation Serif', serif"

# ------------------------------------------------------------------ colours (hex)
COLORS = {
    "navy": "#031923",          # page background behind documents/previews
    "forest_deep": "#06291f",   # darkest brand green: icons, letter banner, launch screen edge
    "forest_lift": "#153d2b",   # lighter green used for the soft glow in icons / banners
    "forest_header": "#092c21", # email header band
    "forest_panel": "#11372a",  # email dark panel
    "forest": "#123b2a",        # print contact box
    "heading": "#123b2b",       # headings on light paper
    "ink": "#203c30",           # body text on light paper
    "muted": "#667269",         # secondary text on light paper
    "faint": "#8a948c",         # tertiary text / unsubscribe links
    "gold": "#e9bd61",          # buttons, labels on dark green
    "gold_line": "#bb9147",     # hairline borders
    "gold_text": "#8b6826",     # gold text on light paper (eyebrows)
    "ivory": "#fffdf7",         # email / print paper
    "ivory_text": "#fbf4e5",    # text on dark green
    "detail_bg": "#f3f5ef",     # light information box
    "footer_bg": "#f0f2e9",     # email footer band
    "footer_line": "#d1d4bf",
    # print (letter / flyer / storm quote): values from the approved print designs
    "print_banner_from": "#06291f", "print_banner_to": "#163e2b",   # dark banner gradient
    "print_panel_from": "#082d22", "print_panel_to": "#173e2c",     # contact-box gradient
    "print_heading": "#123d2b",
    "print_gold_label": "#76591d",    # gold text on white paper
    "print_gold_rule": "#bd974a",     # gold rules / left edges on paper
    "print_gold_on_dark": "#e3bd65",  # gold text on dark green
    "print_ivory_on_dark": "#fff5dd",
    "print_ivory_soft": "#f1eee2",
    "print_paper_tint": "#f3f5ef",    # light tint box
    "link": "#846022",          # links inside emails
    "positive": "#2e6b46",
    "warning": "#9a5b13",
}
THEME_COLOR = COLORS["forest_deep"]        # browser toolbar / Android status bar
BACKGROUND_COLOR = COLORS["forest_deep"]   # launch-screen background (manifest)

# ------------------------------------------------------------------ icons and cache
# Bump after changing any icon / share image so phones and browsers fetch the new files.
ASSET_VERSION = "20261020"


def v(path: str) -> str:
    """'/static/x.png' -> '/static/x.png?v=<ASSET_VERSION>' (cache-busting)."""
    return f"{path}?v={ASSET_VERSION}"


def public_base() -> str:
    """Production site address used in emails and share tags (never a preview/example host)."""
    return (os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")) or f"https://{CANONICAL_HOST}"


# Paths of the files tools/build_brand_assets.py writes (relative to the site root).
ICON_FAVICON_ICO = "/favicon.ico"
ICON_FAVICON_32 = "/static/brand/favicon-32.png"
ICON_FAVICON_16 = "/static/brand/favicon-16.png"
ICON_APPLE_TOUCH = "/static/brand/apple-touch-icon-180.png"
ICON_ANY_192 = "/static/icon-192.png"
ICON_ANY_512 = "/static/icon-512.png"
ICON_MASKABLE_192 = "/static/brand/maskable-192.png"
ICON_MASKABLE_512 = "/static/brand/maskable-512.png"
OG_IMAGE = "/static/images/og-image.png"
EMAIL_CREST = "/static/brand/email-crest.png"
MANIFEST = "/static/manifest.json"

# iPhone launch screens: (device px width, height, css width, css height, pixel ratio)
IOS_SPLASH = [
    (1320, 2868, 440, 956, 3), (1206, 2622, 402, 874, 3), (1290, 2796, 430, 932, 3),
    (1179, 2556, 393, 852, 3), (1170, 2532, 390, 844, 3), (1125, 2436, 375, 812, 3),
    (1242, 2688, 414, 896, 3), (828, 1792, 414, 896, 2), (750, 1334, 375, 667, 2),
]


def email_crest_url() -> str:
    return public_base() + v(EMAIL_CREST)


def icon_links_html() -> str:
    """Browser tab / bookmark / home-screen icons + iPhone launch screens, as <head> tags."""
    tags = [
        f'<link rel="icon" href="{v(ICON_FAVICON_ICO)}" sizes="any">',
        f'<link rel="icon" type="image/png" sizes="32x32" href="{v(ICON_FAVICON_32)}">',
        f'<link rel="icon" type="image/png" sizes="16x16" href="{v(ICON_FAVICON_16)}">',
        f'<link rel="apple-touch-icon" sizes="180x180" href="{v(ICON_APPLE_TOUCH)}">',
        '<meta name="apple-mobile-web-app-capable" content="yes">',
    ]
    for w, h, cw, ch, r in IOS_SPLASH:
        tags.append(
            f'<link rel="apple-touch-startup-image" href="{v(f"/static/brand/splash/ios-{w}x{h}.png")}" '
            f'media="(device-width: {cw}px) and (device-height: {ch}px) and (-webkit-device-pixel-ratio: {r}) '
            f'and (orientation: portrait)">'
        )
    return "\n".join(tags)


# Public pages that should carry link-preview (Open Graph / Twitter) tags if they have none yet.
SOCIAL_PATHS = frozenset({"/", "/marketplace", "/pricing", "/faq", "/free-account",
                          "/privacy-policy", "/terms-of-service"})

_OLD_ICON_LINK = re.compile(r'<link\s+rel=["\'](?:shortcut\s+)?icon["\'][^>]*>\s*', re.I)
_OLD_TOUCH_LINK = re.compile(r'<link\s+rel=["\']apple-touch-icon["\'][^>]*>\s*', re.I)
_THEME_META = re.compile(r'<meta\s+name=["\']theme-color["\'][^>]*>', re.I)
_MANIFEST_LINK = re.compile(r'(<link\s+rel=["\']manifest["\']\s+href=["\'])([^"\']+)(["\'])', re.I)
_OG_IMAGE_URL = re.compile(r'(content=["\'])https?://[^"\']*/static/images/og-image\.png(?:\?[^"\']*)?(["\'])', re.I)
_TITLE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
_HEAD_CLOSE = re.compile(r"</head\s*>", re.I)


def enabled() -> bool:
    return os.getenv("TREEKEY_BRAND_HEAD", "on").strip().lower() not in ("off", "0", "false", "no")


def _social_tags(html_text: str, path: str) -> str:
    base = f"https://{CANONICAL_HOST}"
    image = base + v(OG_IMAGE)
    m = _TITLE.search(html_text)
    title = (m.group(1).strip() if m else OG_TITLE)
    d = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']', html_text, re.I)
    desc = d.group(1) if d else OG_DESCRIPTION
    url = base + (path if path != "/" else "/")
    q = lambda s: s.replace('"', "&quot;")  # noqa: E731
    return "\n".join([
        f'<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{NAME}">',
        f'<meta property="og:title" content="{q(title)}">',
        f'<meta property="og:description" content="{q(desc)}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{image}">',
        f'<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:image" content="{image}">',
    ])


def apply_head_branding(html_text: str, path: str = "") -> str:
    """Swap the old icon links / theme colour for the current brand set, version the manifest and
    share-image URLs, and add link-preview tags on public pages that have none.
    Touches <head> only. Returns the input unchanged if it has no </head>."""
    if not enabled() or not _HEAD_CLOSE.search(html_text):
        return html_text
    out = _OLD_ICON_LINK.sub("", html_text)
    out = _OLD_TOUCH_LINK.sub("", out)
    out = _THEME_META.sub(f'<meta name="theme-color" content="{THEME_COLOR}">', out)
    out = _MANIFEST_LINK.sub(lambda m: m.group(1) + MANIFEST + f"?v={ASSET_VERSION}" + m.group(3), out)
    out = _OG_IMAGE_URL.sub(lambda m: m.group(1) + f"https://{CANONICAL_HOST}" + v(OG_IMAGE) + m.group(2), out)
    extra = icon_links_html()
    if not _THEME_META.search(html_text):
        extra += f'\n<meta name="theme-color" content="{THEME_COLOR}">'
    if path in SOCIAL_PATHS and 'property="og:image"' not in out:
        extra += "\n" + _social_tags(out, path)
    return _HEAD_CLOSE.sub(lambda m: extra + "\n" + m.group(0), out, count=1)
