#!/usr/bin/env python3
# Copyright (c) 2026 John Carter. All rights reserved.
"""Generate the Channel desktop app icon.

Renders the brand mark (orange rounded square + two white pause bars)
as a 1024×1024 transparent-margin PNG and packages it into a macOS
``.icns`` icon set. Run when the brand mark changes; otherwise the
generated assets at ``desktop/resources/icon.{png,icns}`` are
committed and used by the Electron build.

Prerequisites:
    uv sync --group desktop-tools   # installs Pillow

Usage:
    uv run python desktop/scripts/generate_icon.py

The script writes ``desktop/resources/icon.png`` and (on macOS only)
``desktop/resources/icon.icns``. On non-Darwin hosts the PNG is
regenerated and a warning is emitted that ICNS generation was
skipped because ``iconutil`` is macOS-only.

Brand spec lives in
``docs/superpowers/specs/2026-06-01-desktop-icon-full-bleed-design.md``.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

# ---- Brand constants --------------------------------------------------------

CANVAS_SIZE = 1024
CONTENT_INSET = 100  # Apple HIG-style content inset
CONTENT_SIZE = CANVAS_SIZE - 2 * CONTENT_INSET  # 824
CORNER_RADIUS_RATIO = 0.26  # matches ChannelMark.jsx (size * 0.26)
# sRGB for oklch(0.585 0.130 42) — the light-theme --accent token
# in ui/src/styles/channel.css. Conversion performed offline via
# Björn Ottosson's OKLab → linear sRGB matrix + the standard sRGB
# gamma curve. See spec §Component changes.
ACCENT_FILL = (186, 94, 56, 255)  # #ba5e38
ON_ACCENT_FILL = (255, 255, 255, 255)
# Pause-bar geometry inherits from ChannelMark.jsx's SVG (viewBox 0..24):
# two bars at x=6.6 and x=14.3, width=3.1, y=6, height=12, rx=1.2.
BAR_VIEWBOX = 24.0
BAR_X1_VB = 6.6
BAR_X2_VB = 14.3
BAR_W_VB = 3.1
BAR_Y_VB = 6.0
BAR_H_VB = 12.0
BAR_RX_VB = 1.2

ICONSET_SIZES = [
    # (filename, side length)
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_DIR = REPO_ROOT / "desktop" / "resources"
PNG_PATH = RESOURCES_DIR / "icon.png"
ICNS_PATH = RESOURCES_DIR / "icon.icns"


def _vb_to_canvas(vb: float) -> float:
    """Translate a viewBox (0..24) coord to canvas px inside the inset square."""
    return CONTENT_INSET + (vb / BAR_VIEWBOX) * CONTENT_SIZE


def render_icon(size: int) -> Image.Image:
    """Render the icon at the given square ``size`` (px).

    The brand geometry is anchored to the 1024-px canvas; smaller
    sizes are rendered by drawing into a 1024 buffer and resizing
    with Pillow's high-quality LANCZOS filter.
    """
    img = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Orange rounded square (the mark's body).
    outer_radius = int(CONTENT_SIZE * CORNER_RADIUS_RATIO)
    draw.rounded_rectangle(
        [
            (CONTENT_INSET, CONTENT_INSET),
            (CONTENT_INSET + CONTENT_SIZE, CONTENT_INSET + CONTENT_SIZE),
        ],
        radius=outer_radius,
        fill=ACCENT_FILL,
    )

    # Two white pause bars.
    bar_width = (BAR_W_VB / BAR_VIEWBOX) * CONTENT_SIZE
    bar_height = (BAR_H_VB / BAR_VIEWBOX) * CONTENT_SIZE
    bar_radius = int((BAR_RX_VB / BAR_VIEWBOX) * CONTENT_SIZE)
    bar_y0 = _vb_to_canvas(BAR_Y_VB)
    bar_y1 = bar_y0 + bar_height
    for x_vb in (BAR_X1_VB, BAR_X2_VB):
        x0 = _vb_to_canvas(x_vb)
        x1 = x0 + bar_width
        draw.rounded_rectangle(
            [(x0, bar_y0), (x1, bar_y1)],
            radius=bar_radius,
            fill=ON_ACCENT_FILL,
        )

    if size != CANVAS_SIZE:
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    return img


def write_png(path: Path) -> None:
    """Write the master 1024×1024 PNG to ``path``."""
    img = render_icon(CANVAS_SIZE)
    img.save(path, "PNG", optimize=True)
    print(f"Wrote {path} ({CANVAS_SIZE}x{CANVAS_SIZE})", file=sys.stderr)


def write_icns(path: Path) -> bool:
    """Build a macOS .icns from per-size renders.

    Returns False (with a warning) when iconutil isn't available
    (i.e. non-macOS host).
    """
    if platform.system() != "Darwin" or shutil.which("iconutil") is None:
        print(
            "warning: iconutil unavailable; skipping .icns generation",
            file=sys.stderr,
        )
        return False
    with tempfile.TemporaryDirectory() as tmpdir:
        iconset = Path(tmpdir) / "icon.iconset"
        iconset.mkdir()
        for filename, side in ICONSET_SIZES:
            render_icon(side).save(iconset / filename, "PNG", optimize=True)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(path)],
            check=True,
        )
    print(f"Wrote {path}", file=sys.stderr)
    return True


def main() -> int:
    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    write_png(PNG_PATH)
    write_icns(ICNS_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
