# Desktop app icon — full-bleed brand mark — design

**Date:** 2026-06-01
**Status:** Approved (ready for plan)

## Summary

The current macOS app icon (`desktop/resources/icon.png`) renders the orange Channel mark inside a separate white squircle, producing a "small rounded square inside a big rounded square" visual. In Finder's Applications view the Channel - Dev tile is the only icon with a bright-white background — it stands out as wrong next to App Store, Calendar, Books, etc., which all use a coloured fill matching their brand.

Regenerate the icon so the Channel mark IS the icon — full-bleed orange rounded square (at the standard ~80% canvas inset) with two white pause bars inside. macOS now sees a proper branded squircle and the icon visually matches its peers.

## Goals

- The Finder Applications view shows Channel - Dev as a clean orange squircle (with two white pause bars) — no white outer fill.
- Brand colors come directly from the existing `ChannelMark` SVG source — no new design assets.
- Both PNG (used by Linux + as Electron `mainWindow` icon) and ICNS (used by macOS `.app`) regenerated from the same source.

## Non-goals

- Windows `.ico` regeneration — same brand source, can be added in a follow-up.
- Dark-mode-specific icon variant — macOS doesn't support per-mode app icons natively.
- Brand redesign — using the existing `ChannelMark` mark verbatim.
- Any code logic change in the renderer / main process.

## Architecture

```
┌──────────────────────────────────┐
│ desktop/scripts/generate_icon.py │  Pillow-based generator
│ - reads brand spec constants      │  (colors, geometry, radii)
│ - renders 1024×1024 PNG (RGBA)    │
│ - writes icon.png                 │
│ - shells out to iconutil to       │
│   build icon.icns from a temp     │
│   .iconset dir                    │
└────────────┬─────────────────────┘
             │
             ▼
┌──────────────────────────────────┐
│ desktop/resources/icon.png       │
│ desktop/resources/icon.icns      │
└──────────────────────────────────┘
             │ consumed by
             ▼
┌──────────────────────────────────┐
│ electron-builder.yml (mac.icon)  │
│ → embeds icon.icns in Channel.app│
└──────────────────────────────────┘
```

## Component changes

### `desktop/scripts/generate_icon.py` (new)

Python script using Pillow. Pillow is NOT currently a project dependency (`grep pillow pyproject.toml uv.lock` returns nothing) — it gets added under a new `[project.optional-dependencies] desktop-tools = ["pillow>=11"]` group so the regular install path doesn't grow. Document the install at the top of the script: `uv sync --extra desktop-tools` before running.

Layout:

- Module-level constants for the geometry + colors:
  ```python
  CANVAS_SIZE = 1024
  CONTENT_INSET = 100         # Apple HIG-style content inset
  CONTENT_SIZE = CANVAS_SIZE - 2 * CONTENT_INSET  # 824
  CORNER_RADIUS_RATIO = 0.26  # matches ChannelMark.jsx (size * 0.26)
  # sRGB for oklch(0.585 0.130 42) — the light-theme --accent token
  # in ui/src/styles/channel.css. Conversion performed offline via
  # Björn Ottosson's OKLab → linear sRGB matrix + the standard sRGB
  # gamma curve. See spec §Component changes.
  ACCENT_FILL = (186, 94, 56, 255)  # #ba5e38
  ON_ACCENT_FILL = (255, 255, 255, 255)
  # Pause-bar geometry inherits from the SVG: viewBox 0..24
  # bars at x=6.6 and x=14.3, width=3.1, y=6, height=12, rx=1.2.
  BAR_VIEWBOX = 24.0
  ```
- The sRGB constant is precomputed once via the offline conversion; the script doesn't need a color-space library at runtime.
- `render_icon_png(path)`: build an `Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))`, draw a rounded-rectangle filled with `ACCENT_FILL`, draw two rounded white bars per the viewBox math, save as PNG.
- `render_icon_icns(png_path, icns_path)`: create a temp `.iconset` directory containing the standard 10 sizes Apple expects (16, 16@2x, 32, 32@2x, 128, 128@2x, 256, 256@2x, 512, 512@2x), shell out to `iconutil -c icns <iconset>`. `iconutil` is macOS-only; the script skips ICNS generation gracefully on non-Darwin and emits a warning, letting CI on Linux runners regenerate the PNG without failing.
- `main()`: regenerates both files in place at `desktop/resources/`.

The script is idempotent: re-running with the same constants produces byte-identical output. It's checked-in code, not part of the build pipeline — re-run only when the brand mark changes.

### `desktop/resources/icon.png` (regenerated)

1024×1024 RGBA. Transparent margin around an orange rounded square (824×824, corner radius ~214px) with two white pause bars centred inside.

### `desktop/resources/icon.icns` (regenerated)

macOS icon set with all 10 standard sizes (16 / 32 / 128 / 256 / 512 at 1× and 2×), each containing the scaled rendering. `electron-builder.yml`'s `mac.icon` resolves to this file when packaging the `.app`.

### `CHANGELOG.md`

Single bullet under `[Unreleased]` `### Changed`:

> macOS app icon regenerated as a full-bleed brand mark (orange squircle with two white pause bars). The previous icon rendered the brand mark inside a separate white squircle, producing a "rounded square in a rounded square" look that stood out next to other apps in Finder. The new icon matches the macOS app-icon convention used by Calendar, Books, App Store, etc.

## Validation

### Layer 1 — Unit (script self-check)

- Run `uv run python desktop/scripts/generate_icon.py --output /tmp/icon-probe.png`. Assert exit code 0 and that the produced PNG is 1024×1024 RGBA.
- `desktop/test/` already runs vitest in node environment for the main-process modules; adding image-pixel assertions there is overkill for a static asset. Visual validation is the gate.

### Layer 2 — Visual

- `open desktop/resources/icon.png` — confirm the rendered icon is the expected orange squircle with two white bars, transparent corners.
- `uv run inv desktop-build --platform mac` — produces an unsigned `.dmg` containing the new icon. Mount it; verify the icon in the DMG window root matches the design and that copying `Channel.app` to `/Applications` shows the same icon in Finder.

### Layer 3 — Live (post-deploy)

After the PR merges and the dev publish workflow ships a fresh signed/notarized `Channel-Dev` build:

1. Download Channel-Dev from the dev channel.
2. Drag into `/Applications`.
3. Open Finder → Applications → confirm the Channel - Dev icon matches the surrounding apps' style (coloured squircle, no white fill).

## Failure modes

- **Pillow not present**: the script's first run will `ImportError` cleanly. The script's docstring mentions the install command (`uv pip install pillow` or whatever the project uses). Pillow is a one-time dev dependency for the icon generator.
- **`iconutil` unavailable** (non-macOS environment): script falls through with a warning; PNG still updates. CI on Linux can still regenerate the PNG; the ICNS is regenerated by humans on a Mac when the brand changes.
- **Re-running on a different platform** (e.g. Linux): produces an `icon.png` that diffs against the macOS-generated one only if a Pillow version difference changes pixel output. Lock `pillow` to a specific version in the script's docstring to keep results reproducible.

## CHANGELOG entry (draft)

```
### Changed
- macOS app icon regenerated as a full-bleed brand mark (orange
  squircle with two white pause bars). The previous icon rendered the
  brand mark inside a separate white squircle, producing a "rounded
  square in a rounded square" look that stood out next to other apps
  in Finder. The new icon matches the macOS app-icon convention used
  by Calendar, Books, App Store, etc.
```

## Out of scope / deferred

- Windows `.ico` regeneration (`desktop/resources/icon.ico` if/when it exists).
- Tray / menubar icon variants.
- macOS icon's wallpaper-tinted "monochrome" Sonoma-style variant.
- A Mac+iOS icon-set with different sizes for App Store Connect.
