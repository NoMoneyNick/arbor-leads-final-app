"""
generate_qr_codes.py -- Sep 3 2026

Standalone, offline tool (like bulk_contractor_extractor.py -- NOT part of
the live app, NOT called by any FastAPI route, NOT deployed to Render).
Run this locally whenever Nick needs printable QR codes for a new batch of
marketing material (a business card run, a trade-show stand, a future
letter campaign -- see MARKETING_OUTREACH_IDEAS.md).

Each QR code encodes:
    https://treekey.uk/partner-offer?src=<campaign-code>

Scanning it lands the person on the /partner-offer landing page (main.py),
and the `src` code is recorded against every interest-capture form
submission from that page -- so Nick can tell which printed batch/channel
is actually generating interest via GET /trigger-qr-campaign-stats?secret=...
without ever needing per-recipient tracking or a live QR-generation
endpoint in the production app.

Deliberately uses only reportlab (already vendored for other parts of this
project's tooling) for the actual QR encoding/error-correction math -- the
hard, easy-to-get-subtly-wrong part -- and plain Pillow for rasterizing the
resulting module matrix into a PNG. Verified end-to-end this session: every
generated PNG round-trips through OpenCV's QRCodeDetector back to the exact
original URL before being treated as good. NOT added to requirements.txt
(reportlab/Pillow) since this script never runs as part of the live web
app -- only locally, on demand.

Usage:
    python3 generate_qr_codes.py
    python3 generate_qr_codes.py "my-custom-code" "another-code"
"""
import sys
import os

try:
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from PIL import Image
except ImportError:
    print("Missing dependencies. Run locally with:")
    print("  pip install reportlab pillow --break-system-packages")
    sys.exit(1)

BASE_URL = "https://treekey.uk/partner-offer"
OUTPUT_DIR = "qr_codes_output"

# Default campaign codes -- edit/extend freely, or pass custom codes as
# command-line arguments instead.
DEFAULT_CAMPAIGNS = ["business-card", "trade-show", "email-signature", "letter-test"]


def make_qr_png(data: str, path: str, box_size: int = 10, border: int = 4) -> None:
    """Encodes `data` as a QR code and writes it to `path` as a PNG.

    box_size: pixels per QR module (higher = larger, crisper print).
    border: quiet-zone width in modules (4 is the QR spec's minimum --
    printers/scanners can misread a code with less white space around it).
    """
    qr = QrCodeWidget(data)
    qr.draw()  # populates qr.qr.modules -- accessing it before this call
    # silently returns an empty 0x0 matrix, confirmed live this session.
    modules = qr.qr.modules
    n = qr.qr.getModuleCount()
    img_size = (n + border * 2) * box_size
    img = Image.new("RGB", (img_size, img_size), "white")
    pixels = img.load()
    for r in range(n):
        for c in range(n):
            if modules[r][c]:
                x0 = (c + border) * box_size
                y0 = (r + border) * box_size
                for dx in range(box_size):
                    for dy in range(box_size):
                        pixels[x0 + dx, y0 + dy] = (0, 0, 0)
    img.save(path)


def main():
    codes = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_CAMPAIGNS
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for code in codes:
        url = f"{BASE_URL}?src={code}"
        out_path = os.path.join(OUTPUT_DIR, f"qr_{code}.png")
        make_qr_png(url, out_path)
        print(f"✅ {out_path}  ->  {url}")
    print(f"\n{len(codes)} QR code(s) written to ./{OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
