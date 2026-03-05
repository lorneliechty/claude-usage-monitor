#!/usr/bin/env python3
"""Generate app icon (AppIcon.icns) for Claude Usage Monitor."""

import os
import subprocess
import tempfile
from PIL import Image, ImageDraw


def create_icon_image(size):
    """Create a single icon image at the given size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size  # shorthand

    # Background: rounded dark square
    margin = max(1, int(s * 0.08))
    radius = max(1, int(s * 0.18))
    draw.rounded_rectangle(
        [margin, margin, s - margin, s - margin],
        radius=radius,
        fill=(30, 30, 35, 255),
    )
    # Subtle border
    bw = max(1, s // 128)
    draw.rounded_rectangle(
        [margin, margin, s - margin, s - margin],
        radius=radius,
        outline=(80, 80, 90, 255),
        width=bw,
    )

    # Battery body
    bat_w = int(s * 0.50)
    bat_h = int(s * 0.24)
    bx = (s - bat_w) // 2
    by = int(s * 0.22)
    brad = max(1, int(s * 0.03))
    outline_w = max(1, s // 64)

    draw.rounded_rectangle(
        [bx, by, bx + bat_w, by + bat_h],
        radius=brad,
        outline=(220, 220, 230, 255),
        width=outline_w,
    )

    # Battery cap (only draw if big enough)
    if s >= 32:
        cap_w = max(2, int(s * 0.035))
        cap_h = max(2, int(bat_h * 0.35))
        cap_x = bx + bat_w + max(1, s // 128)
        cap_y = by + (bat_h - cap_h) // 2
        if cap_y + cap_h > cap_y and cap_x + cap_w > cap_x:
            draw.rounded_rectangle(
                [cap_x, cap_y, cap_x + cap_w, cap_y + cap_h],
                radius=max(1, brad // 2),
                fill=(220, 220, 230, 255),
            )

    # Battery fill (green, ~70%)
    fm = outline_w + 1
    fill_pct = 0.7
    il = bx + fm
    it = by + fm
    ir = bx + bat_w - fm
    ib = by + bat_h - fm
    fw = int((ir - il) * fill_pct)

    if fw > 0 and ib > it:
        draw.rounded_rectangle(
            [il, it, il + fw, ib],
            radius=max(1, brad // 2),
            fill=(48, 209, 88, 255),
        )

    # Lightning bolt below battery
    cx = s / 2.0
    bolt_top = by + bat_h + max(2, int(s * 0.06))
    bolt_h = int(s * 0.35)
    bolt_w = int(s * 0.20)

    if bolt_h > 4 and bolt_w > 2:
        points = [
            (cx + bolt_w * 0.15, bolt_top),
            (cx - bolt_w * 0.35, bolt_top + bolt_h * 0.48),
            (cx + bolt_w * 0.05, bolt_top + bolt_h * 0.42),
            (cx - bolt_w * 0.12, bolt_top + bolt_h),
            (cx + bolt_w * 0.35, bolt_top + bolt_h * 0.52),
            (cx - bolt_w * 0.05, bolt_top + bolt_h * 0.58),
        ]
        draw.polygon(points, fill=(255, 204, 0, 255))

    return img


def create_icns(output_path):
    """Create an .icns file with all required sizes."""
    # iconutil expects specific filenames in the iconset
    icon_specs = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        iconset_dir = os.path.join(tmpdir, "AppIcon.iconset")
        os.makedirs(iconset_dir)

        # Cache rendered images by size
        cache = {}
        for px, filename in icon_specs:
            if px not in cache:
                cache[px] = create_icon_image(px)
            cache[px].save(os.path.join(iconset_dir, filename))

        # Use iconutil to create .icns (macOS only)
        try:
            result = subprocess.run(
                ["iconutil", "-c", "icns", iconset_dir, "-o", output_path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
        except (FileNotFoundError, RuntimeError) as e:
            print(f"iconutil unavailable or failed: {e}")
            # Fallback: save a PNG
            img = create_icon_image(256)
            png_path = output_path.replace(".icns", ".png")
            img.save(png_path)
            print(f"Saved fallback PNG: {png_path}")
            return png_path

        return output_path


if __name__ == "__main__":
    out = create_icns("AppIcon.icns")
    print(f"Created: {out}")
