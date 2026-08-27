"""Generate PWA install icons from the existing 192x192 favicon.

Run once (idempotent):
    python backend/gen_pwa_icons.py

Produces in css/images/:
    icon-192.png        192x192  (purpose: any)
    icon-512.png        512x512  (purpose: any)
    maskable-512.png    512x512  (purpose: maskable, logo centered ~60% on a
                                 full-bleed #0f172a background -> safe zone)

Source: css/images/favicon.png (192x192 RGB). No external deps beyond Pillow.
"""
import os
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "css", "images", "favicon.png")
OUT_DIR = os.path.join(ROOT, "css", "images")
BG = (15, 23, 42)  # #0f172a — matches the app's dark UI / splash background


def _save(img, name):
    img = img.convert("RGB")
    img.save(os.path.join(OUT_DIR, name), "PNG")
    print("wrote", name, img.size)


def main():
    src = Image.open(SRC).convert("RGB")
    if src.size != (192, 192):
        src = src.resize((192, 192), Image.LANCZOS)

    # any-purpose icons
    _save(src.copy(), "icon-192.png")
    _save(src.resize((512, 512), Image.LANCZOS), "icon-512.png")

    # maskable: full-bleed background, logo in the central ~60% safe zone
    canvas = Image.new("RGB", (512, 512), BG)
    logo = src.resize((int(512 * 0.60), int(512 * 0.60)), Image.LANCZOS)
    off = ((512 - logo.width) // 2, (512 - logo.height) // 2)
    canvas.paste(logo, off)
    _save(canvas, "maskable-512.png")


if __name__ == "__main__":
    main()