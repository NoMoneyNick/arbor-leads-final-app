"""
tools/build_brand_assets.py -- regenerates every TreeKey brand image from ONE master crest.

RUN (developer machine only; needs Pillow, which the website itself does NOT need):
    python tools/build_brand_assets.py

INPUT   assets/brand/treekey-crest-master.png   (the approved transparent crest)
OUTPUT  static/icon-192.png, static/icon-512.png         phone/PWA icons ("any" purpose)
        static/brand/maskable-192.png, maskable-512.png  Android adaptive icons (crest inside the safe circle)
        static/brand/apple-touch-icon-180.png            iPhone/iPad home-screen icon
        static/favicon.ico + static/brand/favicon-16/32.png   browser tab / bookmark
        static/images/og-image.png                       1200x630 shared-link picture
        static/brand/email-crest.png                     crest for email headers
        static/brand/splash/ios-*.png                    iPhone launch screens
        assets/letter_branding/treekey-crest.png         crest for printed letters

TO RESTYLE: change the values in the COLOURS block below (keep them equal to brand.py), re-run,
then bump brand.ASSET_VERSION so phones and browsers fetch the new files.
"""
import math
import os
import sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import brand  # noqa: E402  (single source of truth for colours + wording)


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---- COLOURS (come from brand.py) ----------------------------------------------------------
FOREST_DEEP = _rgb(brand.COLORS["forest_deep"])
FOREST_LIFT = _rgb(brand.COLORS["forest_lift"])
GOLD = _rgb(brand.COLORS["gold"])
GOLD_LINE = _rgb(brand.COLORS["gold_line"])
IVORY = _rgb(brand.COLORS["ivory_text"])

SERIF = "/usr/share/fonts/truetype/crosextra/Caladea-Bold.ttf"      # stand-in for Georgia on this machine
SERIF_REG = "/usr/share/fonts/truetype/crosextra/Caladea-Regular.ttf"
SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

MASTER = Image.open(os.path.join(ROOT, "assets", "brand", "treekey-crest-master.png")).convert("RGBA")
_BBOX = MASTER.split()[3].point(lambda v: 255 if v > 40 else 0).getbbox()
CREST = MASTER.crop(_BBOX)                       # tight crop of the wreath
_cx, _cy = CREST.size[0] / 2, CREST.size[1] / 2
_px = CREST.split()[3].point(lambda v: 255 if v > 40 else 0).load()
CREST_RADIUS = max(math.hypot(x - _cx, y - _cy) for y in range(CREST.size[1]) for x in range(CREST.size[0]) if _px[x, y])


def out(*parts):
    p = os.path.join(ROOT, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def crest_scaled(radius_px):
    """Crest resized so its furthest pixel from the centre lands at radius_px."""
    k = radius_px / CREST_RADIUS
    return CREST.resize((max(1, round(CREST.size[0] * k)), max(1, round(CREST.size[1] * k))), Image.LANCZOS)


def paste_centered(base, img, cx, cy):
    base.alpha_composite(img, (round(cx - img.size[0] / 2), round(cy - img.size[1] / 2)))


def radial_bg(w, h, cx=0.5, cy=0.45, inner=FOREST_LIFT, outer=FOREST_DEEP, spread=0.75):
    """Soft forest-green glow (same idea as the approved launch-screen mock)."""
    small = 96
    sw, sh = small, max(1, round(small * h / w))
    im = Image.new("RGB", (sw, sh))
    px = im.load()
    for y in range(sh):
        for x in range(sw):
            d = math.hypot((x / sw - cx), (y / sh - cy) * h / w) / spread
            t = min(1, d)
            px[x, y] = tuple(round(inner[i] + (outer[i] - inner[i]) * t) for i in range(3))
    return im.resize((w, h), Image.BICUBIC).convert("RGBA")


def square_icon(size, *, radius_frac, crest_radius_frac, border=True, ss=4):
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=S * radius_frac, fill=255)
    bg = radial_bg(S, S, 0.5, 0.45, FOREST_LIFT, FOREST_DEEP, 0.8)
    img.paste(bg, (0, 0), mask)
    if border:
        d = ImageDraw.Draw(img)
        w = max(2, round(S * 0.012))
        d.rounded_rectangle([w / 2, w / 2, S - 1 - w / 2, S - 1 - w / 2], radius=S * radius_frac, outline=GOLD_LINE, width=w)
    paste_centered(img, crest_scaled(S * crest_radius_frac), S / 2, S / 2)
    return img.resize((size, size), Image.LANCZOS)


def full_bleed_icon(size, crest_radius_frac, ss=3):
    S = size * ss
    img = radial_bg(S, S, 0.5, 0.45, FOREST_LIFT, FOREST_DEEP, 0.8)
    paste_centered(img, crest_scaled(S * crest_radius_frac), S / 2, S / 2)
    return img.resize((size, size), Image.LANCZOS).convert("RGB")


def spaced_text(draw, xy, text, font, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x


def text_width_spaced(draw, text, font, spacing):
    return sum(draw.textlength(c, font=font) + spacing for c in text) - spacing


def build_icons():
    # "any" icons: rounded square with a hairline gold edge; crest fills ~46% of the width on each side
    for s in (192, 512):
        square_icon(s, radius_frac=0.22, crest_radius_frac=0.45).save(out("static", f"icon-{s}.png"), optimize=True)
    # maskable: full-bleed (the phone cuts its own shape). Everything must sit inside the central
    # 80% circle => radius <= 0.40 of the width. We use 0.37 for a small safety margin.
    for s in (192, 512):
        full_bleed_icon(s, 0.37).save(out("static", "brand", f"maskable-{s}.png"), optimize=True)
    full_bleed_icon(180, 0.40).save(out("static", "brand", "apple-touch-icon-180.png"), optimize=True)
    # favicons: crest as large as possible, no hairline (it would swallow detail at 16px)
    fav = {s: square_icon(s, radius_frac=0.2, crest_radius_frac=0.5, border=False, ss=8) for s in (16, 32, 48)}
    fav[16].save(out("static", "brand", "favicon-16.png"), optimize=True)
    fav[32].save(out("static", "brand", "favicon-32.png"), optimize=True)
    fav[48].save(out("static", "brand", "favicon-48.png"), optimize=True)
    big = square_icon(256, radius_frac=0.2, crest_radius_frac=0.5, border=False, ss=4)
    big.save(out("static", "favicon.ico"), format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])


def build_og():
    W, H = 1200, 630
    img = Image.new("RGBA", (W, H), FOREST_DEEP + (255,))
    # soft corner glow (bottom-right), as in the approved mock
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.45, H * 0.35, W * 1.35, H * 1.5], fill=(40, 77, 50, 150))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    d = ImageDraw.Draw(img)
    pad = 75
    crest = crest_scaled(135)                                   # ~270px wide
    paste_centered(img, crest, pad + crest.size[0] / 2, H / 2 - 12)
    tx = pad + crest.size[0] + 56
    f_brand = ImageFont.truetype(SERIF_REG, 92)
    f_tag = ImageFont.truetype(SANS, 19)
    f_head = ImageFont.truetype(SERIF_REG, 50)
    y = 130
    d.text((tx, y), brand.NAME, font=f_brand, fill=IVORY)
    y += 118
    spaced_text(d, (tx, y), brand.TAGLINE, f_tag, GOLD, 3.2)
    y += 62
    for line in brand.OG_HEADLINE_LINES:
        d.text((tx, y), line, font=f_head, fill=IVORY)
        y += 66
    d.rectangle([0, H - 6, W, H], fill=GOLD_LINE)
    img.convert("RGB").save(out("static", "images", "og-image.png"), optimize=True)


def build_email_and_letter():
    w = 132                                          # 66px shown, 2x for sharp screens
    e = CREST.resize((w, round(w * CREST.size[1] / CREST.size[0])), Image.LANCZOS)
    e.save(out("static", "brand", "email-crest.png"), optimize=True)
    # Printed letters embed this image (as base64) into every letter, so keep the file small:
    # 300px wide (~400 dpi at the 19mm print size), 128 colours.
    w = 300
    p = CREST.resize((w, round(w * CREST.size[1] / CREST.size[0])), Image.LANCZOS)
    p.quantize(colors=128, method=Image.FASTOCTREE, dither=Image.NONE).save(
        out("assets", "letter_branding", "treekey-crest.png"), optimize=True)


IOS_SPLASH = [  # width x height in device pixels (portrait), css width/height, pixel ratio
    (1320, 2868, 440, 956, 3), (1206, 2622, 402, 874, 3), (1290, 2796, 430, 932, 3),
    (1179, 2556, 393, 852, 3), (1170, 2532, 390, 844, 3), (1125, 2436, 375, 812, 3),
    (1242, 2688, 414, 896, 3), (828, 1792, 414, 896, 2), (750, 1334, 375, 667, 2),
]


def build_splash():
    for (w, h, *_rest) in IOS_SPLASH:
        img = radial_bg(w, h, 0.5, 0.45, FOREST_LIFT, FOREST_DEEP, 0.9)
        unit = w / 390.0                                  # design is laid out for a 390pt-wide screen
        crest = crest_scaled(75 * unit)
        cy = h * 0.42
        paste_centered(img, crest, w / 2, cy)
        d = ImageDraw.Draw(img)
        f_brand = ImageFont.truetype(SERIF_REG, round(52 * unit))
        bw = d.textlength(brand.NAME, font=f_brand)
        by = cy + crest.size[1] / 2 + 20 * unit
        d.text((w / 2 - bw / 2, by), brand.NAME, font=f_brand, fill=IVORY)
        f_tag = ImageFont.truetype(SANS, max(9, round(11 * unit)))
        tw = text_width_spaced(d, brand.TAGLINE, f_tag, 2.0 * unit)
        spaced_text(d, (w / 2 - tw / 2, by + 74 * unit), brand.TAGLINE, f_tag, GOLD, 2.0 * unit)
        img.convert("RGB").save(out("static", "brand", "splash", f"ios-{w}x{h}.png"), optimize=True)


if __name__ == "__main__":
    build_icons()
    build_og()
    build_email_and_letter()
    build_splash()
    print("brand assets rebuilt")
