// Copyright (c) 2026 John Carter. All rights reserved.
//
// Static-asset regression guard for the installable-PWA wiring (#437).
//
// There is no JS module behind this feature — it is `ui/index.html`, a JSON
// manifest, and four committed PNGs — so the acceptance criteria are asserted
// against the files on disk instead. This is what stops a future edit from
// silently dropping the manifest link, renaming an icon, or regenerating
// `apple-touch-icon.png` with an alpha channel (which iOS composites to
// black, producing a black-cornered home-screen tile).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (relative) => readFileSync(new URL(relative, import.meta.url));
const readText = (relative) => read(relative).toString("utf8");

const indexHtml = readText("../index.html");
const manifestRaw = readText("../public/manifest.webmanifest");
const manifest = JSON.parse(manifestRaw);

const PNG_SIGNATURE = "89504e470d0a1a0a";

/**
 * Parse width/height/colour-type straight out of a PNG's IHDR chunk so the
 * suite needs no image dependency. Layout: 8-byte signature, 4-byte chunk
 * length, 4-byte "IHDR", then width (u32), height (u32), bit depth (u8),
 * colour type (u8).
 */
function pngHeader(relative) {
  const bytes = read(relative);
  return {
    signature: bytes.subarray(0, 8).toString("hex"),
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
    colorType: bytes.readUInt8(25),
  };
}

describe("manifest.webmanifest", () => {
  it("is valid JSON with the standalone install fields", () => {
    expect(manifest.name).toBe("Channel");
    expect(manifest.short_name).toBe("Channel");
    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url).toBe("/app");
    expect(manifest.scope).toBe("/");
  });

  it("uses the dark --canvas token value for both colours", () => {
    // oklch(0.183 0.007 58) from ui/src/styles/channel.css, in sRGB hex.
    expect(manifest.theme_color).toBe("#15120f");
    expect(manifest.background_color).toBe("#15120f");
  });

  it("stays pinned to the dark --canvas token it claims to mirror", () => {
    // Closes the drift loop: without this, retuning the token in
    // channel.css would leave the manifest and the index.html meta
    // agreeing with each other but silently disagreeing with the app.
    // Neither a <meta> nor JSON can reference a custom property, so the
    // hex is duplicated by necessity — this asserts the source of truth
    // it was derived from has not moved.
    const channelCss = readText("./styles/channel.css");
    const darkCanvas = /\[data-theme="dark"\][^}]*?--canvas:\s*([^;]+);/.exec(channelCss);
    expect(darkCanvas).not.toBeNull();
    expect(darkCanvas[1].trim()).toBe("oklch(0.183 0.007 58)");
  });

  it("declares 192, 512 and a maskable 512 icon", () => {
    expect(manifest.icons.map((icon) => icon.src)).toEqual([
      "/icon-192.png",
      "/icon-512.png",
      "/icon-512-maskable.png",
    ]);
    for (const icon of manifest.icons) {
      expect(icon.type).toBe("image/png");
      const header = pngHeader(`../public${icon.src}`);
      expect(header.signature).toBe(PNG_SIGNATURE);
      expect(`${header.width}x${header.height}`).toBe(icon.sizes);
    }
    expect(manifest.icons.filter((i) => i.purpose === "maskable")).toHaveLength(1);
  });
});

describe("index.html PWA wiring", () => {
  // Parsed rather than string-matched so a formatting-only edit to
  // index.html (attribute order, `>` vs `/>`, wrapping) can't fail the
  // suite — only an actual change to the wiring can.
  const doc = new DOMParser().parseFromString(indexHtml, "text/html");
  const meta = (name) => doc.querySelector(`meta[name="${name}"]`)?.getAttribute("content");
  const linkHref = (rel) => doc.querySelector(`link[rel="${rel}"]`)?.getAttribute("href");

  it("opts the viewport into the safe-area insets", () => {
    expect(meta("viewport")).toMatch(/(^|,)\s*viewport-fit=cover\s*($|,)/);
  });

  it("links the manifest and the Apple touch icon", () => {
    expect(linkHref("manifest")).toBe("/manifest.webmanifest");
    expect(linkHref("apple-touch-icon")).toBe("/apple-touch-icon.png");
  });

  it("declares standalone display and theme colour", () => {
    expect(meta("apple-mobile-web-app-capable")).toBe("yes");
    expect(meta("mobile-web-app-capable")).toBe("yes");
    expect(meta("apple-mobile-web-app-title")).toBe("Channel");
    // Must agree with the manifest, which the token test below pins to
    // the dark --canvas value.
    expect(meta("theme-color")).toBe(manifest.theme_color);
  });

  it("sets no apple-mobile-web-app-status-bar-style (#516)", () => {
    // `black-translucent` starts the standalone web view at y=0 without iOS
    // growing it to the full screen height, so the height gained under the
    // status bar is lost as unpaintable screen below the page. Absence is the
    // fix — any value here (translucent or not) is a regression.
    expect(meta("apple-mobile-web-app-status-bar-style")).toBeUndefined();
  });
});

describe("apple-touch-icon.png", () => {
  it("is a 180x180 PNG with no alpha channel", () => {
    const header = pngHeader("../public/apple-touch-icon.png");
    expect(header.signature).toBe(PNG_SIGNATURE);
    expect(header.width).toBe(180);
    expect(header.height).toBe(180);
    // Colour type 2 = truecolour without alpha. iOS composites any
    // transparency to black rather than honouring it.
    expect(header.colorType).toBe(2);
  });
});

describe("maskable icon", () => {
  it("is opaque so the platform mask never reveals transparent corners", () => {
    expect(pngHeader("../public/icon-512-maskable.png").colorType).toBe(2);
  });
});
