"""Grids over a canvas screenshot. A vision model names a cell; the code turns the cell into a point."""

import base64
import io
import string

from PIL import Image, ImageDraw

COLUMNS, ROWS = 6, 4
SUB = 3


def labels():
    return [f"{string.ascii_uppercase[c]}{r + 1}" for r in range(ROWS) for c in range(COLUMNS)]


def sub_labels():
    return [str(n) for n in range(1, SUB * SUB + 1)]


def _encode(image):
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=80)
    return base64.b64encode(out.getvalue()).decode()


def _grid(image, box, columns, rows, names):
    """Draw a labelled grid over box (x, y, w, h) of image, in image pixels."""
    draw = ImageDraw.Draw(image)
    x, y, w, h = box
    for c in range(columns + 1):
        draw.line([(x + w * c / columns, y), (x + w * c / columns, y + h)], fill=(255, 0, 80), width=2)
    for r in range(rows + 1):
        draw.line([(x, y + h * r / rows), (x + w, y + h * r / rows)], fill=(255, 0, 80), width=2)
    for i, name in enumerate(names):
        cx, cy = x + w * (i % columns) / columns + 4, y + h * (i // columns) / rows + 3
        draw.rectangle([cx - 2, cy - 1, cx + 9 * len(name) + 2, cy + 14], fill=(255, 255, 255))
        draw.text((cx, cy), name, fill=(200, 0, 60))
    return image


def coarse(screenshot_png, rect, scale):
    """The viewport screenshot with a 6x4 grid over the canvas. rect is in CSS pixels; scale maps to image pixels."""
    image = Image.open(io.BytesIO(screenshot_png))
    box = tuple(v * scale for v in (rect["x"], rect["y"], rect["w"], rect["h"]))
    return _encode(_grid(image, box, COLUMNS, ROWS, labels()))


def cell_box(rect, cell):
    """CSS-pixel box of a coarse cell."""
    c, r = string.ascii_uppercase.index(cell[0]), int(cell[1:]) - 1
    w, h = rect["w"] / COLUMNS, rect["h"] / ROWS
    return {"x": rect["x"] + c * w, "y": rect["y"] + r * h, "w": w, "h": h}


def fine(screenshot_png, box, scale):
    """The chosen cell, enlarged, with a 3x3 grid numbered 1 to 9."""
    image = Image.open(io.BytesIO(screenshot_png))
    crop = image.crop(tuple(round(v) for v in (box["x"] * scale, box["y"] * scale,
                                               (box["x"] + box["w"]) * scale, (box["y"] + box["h"]) * scale)))
    crop = crop.resize((max(1, crop.width * 3), max(1, crop.height * 3)))
    return _encode(_grid(crop, (0, 0, crop.width, crop.height), SUB, SUB, sub_labels()))


def point(box, sub):
    """Centre of a sub-cell, in CSS pixels."""
    n = int(sub) - 1
    w, h = box["w"] / SUB, box["h"] / SUB
    return box["x"] + (n % SUB + 0.5) * w, box["y"] + (n // SUB + 0.5) * h
