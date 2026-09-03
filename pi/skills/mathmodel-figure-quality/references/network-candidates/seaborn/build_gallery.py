from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "catalog.json"
OUTPUT = ROOT / "network_basic_gallery.png"


def main() -> None:
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))["templates"]
    width, image_size, caption, margin, columns = 295, 295, 42, 22, 4
    rows = (len(entries) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * width + (columns + 1) * margin, rows * (image_size + caption) + (rows + 1) * margin),
        "white",
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=13)
    for index, entry in enumerate(entries):
        image = Image.open(ROOT.parents[5] / entry["preview_path"]).convert("RGB")
        column, row = index % columns, index // columns
        x = margin + column * (width + margin)
        y = margin + row * (image_size + caption + margin)
        sheet.paste(image, (x, y))
        draw.text((x, y + image_size + 10), entry["id"].removeprefix("seaborn-"), fill="#263238", font=font)
        image.close()
    sheet.save(OUTPUT)


if __name__ == "__main__":
    main()
