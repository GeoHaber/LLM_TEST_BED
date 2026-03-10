"""Generate ZenAIos PWA icons at all required sizes using Pillow."""

from PIL import Image, ImageDraw
import os

SIZES = [72, 96, 128, 144, 152, 192, 384, 512]
OUT_DIR = os.path.join(os.path.dirname(__file__), "icons")
os.makedirs(OUT_DIR, exist_ok=True)


def draw_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background rounded rect
    r = int(size * 0.15)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill="#3B5BDB")

    # Inner rect
    m = int(size * 0.08)
    draw.rounded_rectangle(
        [m, m, size - 1 - m, size - 1 - m], radius=int(r * 0.7), fill="#4C6EF5"
    )

    # Grid squares
    pad = int(size * 0.18)
    gap = int(size * 0.04)
    cell = (size - pad * 2 - gap) // 2
    cr = int(size * 0.05)

    # Top-left
    draw.rounded_rectangle(
        [pad, pad, pad + cell, pad + cell], radius=cr, fill="#FFFFFF"
    )
    # Top-right
    draw.rounded_rectangle(
        [pad + cell + gap, pad, pad + cell * 2 + gap, pad + cell],
        radius=cr,
        fill="#FFFFFF",
    )
    # Bottom-left
    draw.rounded_rectangle(
        [pad, pad + cell + gap, pad + cell, pad + cell * 2 + gap],
        radius=cr,
        fill="#FFFFFF",
    )
    # Bottom-right (semi-transparent)
    draw.rounded_rectangle(
        [
            pad + cell + gap,
            pad + cell + gap,
            pad + cell * 2 + gap,
            pad + cell * 2 + gap,
        ],
        radius=cr,
        fill=(255, 255, 255, 102),
    )

    # Red cross in center
    cx, cy = size // 2, size // 2
    cw = max(2, int(size * 0.04))
    ch = max(6, int(size * 0.18))
    draw.rectangle(
        [cx - cw // 2, cy - ch // 2, cx + cw // 2, cy + ch // 2], fill="#E03131"
    )
    draw.rectangle(
        [cx - ch // 2, cy - cw // 2, cx + ch // 2, cy + cw // 2], fill="#E03131"
    )

    return img


for size in SIZES:
    icon = draw_icon(size)
    path = os.path.join(OUT_DIR, f"icon-{size}.png")
    icon.save(path, "PNG")
    print(f"Generated {path} ({size}x{size})")

print("Done! All icons generated.")
