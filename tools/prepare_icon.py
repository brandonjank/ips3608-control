"""Encode the generated master artwork as app PNGs and a multi-size Windows ICO."""

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "branding" / "icon-master.png"
OUTPUT = ROOT / "ips3608" / "assets"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as image:
        image = image.convert("RGBA")
        assert image.width == image.height, "Icon master must be square."
        assert image.getextrema()[3][0] == 0, "Icon must retain transparent outer pixels."
        for size in (32, 48, 64, 128, 256):
            image.resize((size, size), Image.Resampling.LANCZOS).save(OUTPUT / f"icon-{size}.png")
        image.save(OUTPUT / "app.ico", format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("Prepared transparent PNG and multi-size ICO assets.")


if __name__ == "__main__":
    main()
