"""
tools/brand_check.py -- quick health check of the TreeKey brand files. Run from the project folder:
    python tools/brand_check.py
Checks: icon / favicon / share-image / splash sizes, manifest, files the manifest and head tags point at,
and that brand.apply_head_branding produces the expected tags. Prints OK / PROBLEM lines; exit code 1 on any problem.
Needs Pillow (developer machine only).
"""
import json, os, re, sys
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import brand  # noqa: E402

problems = []


def check(ok, msg):
    print(("OK       " if ok else "PROBLEM  ") + msg)
    if not ok:
        problems.append(msg)


def size(path):
    with Image.open(os.path.join(ROOT, path.lstrip("/"))) as im:
        return im.size


want = {
    "/static/icon-192.png": (192, 192), "/static/icon-512.png": (512, 512),
    "/static/brand/maskable-192.png": (192, 192), "/static/brand/maskable-512.png": (512, 512),
    "/static/brand/apple-touch-icon-180.png": (180, 180),
    "/static/brand/favicon-16.png": (16, 16), "/static/brand/favicon-32.png": (32, 32), "/static/brand/favicon-48.png": (48, 48),
    "/static/images/og-image.png": (1200, 630),
}
for w, h, *_ in brand.IOS_SPLASH:
    want[f"/static/brand/splash/ios-{w}x{h}.png"] = (w, h)
for p, s in want.items():
    try:
        check(size(p) == s, f"{p} is {size(p)} (want {s})")
    except Exception as e:
        check(False, f"{p} missing/unreadable: {e}")

ico = Image.open(os.path.join(ROOT, "static", "favicon.ico"))
check({(16, 16), (32, 32), (48, 48)} <= set(ico.info.get("sizes", {ico.size})), f"favicon.ico sizes {sorted(ico.info.get('sizes', {ico.size}))}")
crest = Image.open(os.path.join(ROOT, "assets", "letter_branding", "treekey-crest.png"))
check(os.path.getsize(os.path.join(ROOT, "assets", "letter_branding", "treekey-crest.png")) < 100_000, f"letter crest {crest.size}, small enough to embed")

m = json.load(open(os.path.join(ROOT, "static", "manifest.json"), encoding="utf-8"))
check(m.get("start_url") == "/dashboard", f"manifest start_url {m.get('start_url')}")
check(m.get("theme_color") == brand.THEME_COLOR and m.get("background_color") == brand.BACKGROUND_COLOR, "manifest colours match brand.py")
purposes = {(i["sizes"], i.get("purpose")) for i in m["icons"]}
check({("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"), ("512x512", "maskable")} <= purposes, "manifest has any + maskable 192/512")
for i in m["icons"]:
    f = i["src"].split("?")[0]
    check(os.path.exists(os.path.join(ROOT, f.lstrip("/"))) and i["src"].endswith("?v=" + brand.ASSET_VERSION), f"manifest icon {i['src']} exists and uses current ASSET_VERSION")

# maskable safe zone: nothing but the green background outside the central circle of radius 40% of the width
im = Image.open(os.path.join(ROOT, "static/brand/maskable-512.png")).convert("RGB")
bg = im.getpixel((2, 2))
outside = 0
px = im.load()
for y in range(0, 512, 2):
    for x in range(0, 512, 2):
        if (x - 256) ** 2 + (y - 256) ** 2 > (0.40 * 512) ** 2:
            r, g, b = px[x, y]
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 90 and g - r < 30:   # gold/ivory artwork, not green glow
                outside += 1
check(outside == 0, f"maskable-512: {outside} artwork pixels outside the safe circle")

sample = '<html><head><title>T</title><link rel="icon" href="/static/icon-192.png"><link rel="apple-touch-icon" href="/static/icon-192.png"><meta name="theme-color" content="#000"><link rel="manifest" href="/static/manifest.json"></head><body></body></html>'
out = brand.apply_head_branding(sample, "/")
for needle in ("favicon.ico", "apple-touch-icon-180.png", brand.THEME_COLOR, "manifest.json?v=" + brand.ASSET_VERSION, "og:image", "apple-touch-startup-image"):
    check(needle in out, f"apply_head_branding adds '{needle}'")
check("icon-192.png\">" not in out, "old icon links removed")
sys.exit(1 if problems else 0)
