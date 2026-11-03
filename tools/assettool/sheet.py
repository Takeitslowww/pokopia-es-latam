from __future__ import annotations

import json
import os

from PIL import Image, ImageDraw

LABEL_H = 28


def _checker(size: tuple[int, int], step: int = 8) -> Image.Image:
    img = Image.new("RGBA", size, (96, 96, 96, 255))
    draw = ImageDraw.Draw(img)
    for y in range(0, size[1], step):
        for x in range((y // step) % 2 * step, size[0], 2 * step):
            draw.rectangle((x, y, x + step - 1, y + step - 1), fill=(128, 128, 128, 255))
    return img


def contact_sheets(assets_dir: str, out_png: str, subdir: str = "es-419", thumb: int = 300,
                   cols: int = 5, per_sheet: int = 30) -> list[str]:
    with open(os.path.join(assets_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    items = [(a["name"], a["category"], os.path.join(assets_dir, subdir, a["name"] + ".png"))
             for a in manifest["assets"]]
    items = [it for it in items if os.path.isfile(it[2])]
    stem, ext = os.path.splitext(out_png)
    written: list[str] = []
    for page, start in enumerate(range(0, len(items), per_sheet), 1):
        chunk = items[start:start + per_sheet]
        rows = -(-len(chunk) // cols)
        sheet = Image.new("RGBA", (cols * (thumb + 8) + 8, rows * (thumb + LABEL_H + 8) + 8),
                          (32, 32, 32, 255))
        draw = ImageDraw.Draw(sheet)
        for i, (name, _cat, path) in enumerate(chunk):
            x = 8 + (i % cols) * (thumb + 8)
            y = 8 + (i // cols) * (thumb + LABEL_H + 8)
            with Image.open(path) as src:
                img = src.convert("RGBA")
            size = img.size
            img.thumbnail((thumb, thumb))
            bg = _checker(img.size)
            bg.alpha_composite(img)
            sheet.paste(bg, (x + (thumb - img.width) // 2, y + LABEL_H + (thumb - img.height) // 2))
            draw.text((x, y + 1), name[:48], fill=(255, 230, 120, 255))
            draw.text((x, y + 14), "%dx%d" % size, fill=(200, 200, 200, 255))
        path = out_png if page == 1 else "%s_%d%s" % (stem, page, ext)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        sheet.convert("RGB").save(path, format="PNG")
        written.append(path)
    return written
