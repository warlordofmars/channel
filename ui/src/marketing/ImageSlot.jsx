// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * Marketing image. When `src` is provided, renders a real <img> at its
 * natural aspect ratio (the consumer's CSS class — e.g. `.hero-shot` —
 * controls width / max-width); otherwise renders a placeholder block
 * tagged `data-image-slot="<name>"` so a future content pass can find
 * every empty slot.
 *
 * Use:
 *   <ImageSlot name="hero-screen-light" src="/screens/hero-home-light.png" alt="…" />
 *   <ImageSlot name="blog-cover-1" aspect="3 / 2" className="rounded-lg" />
 *
 * The `aspect` prop only governs the placeholder block; with a real `src`
 * the image renders at natural proportions, so cropping that doesn't
 * match the mockup (e.g. 16:9 cover on a 1340x860 capture) doesn't
 * silently mangle the asset.
 */
export default function ImageSlot({ name, aspect = "16 / 9", className = "", src, alt = "" }) {
  if (src) {
    return (
      <img
        className={`image-slot ${className}`.trim()}
        data-image-slot={name}
        src={src}
        alt={alt}
      />
    );
  }
  return (
    <div
      className={`image-slot ${className}`.trim()}
      data-image-slot={name}
      style={{
        aspectRatio: aspect,
        background: "var(--raised-2)",
        border: "1px dashed var(--border)",
        borderRadius: "var(--r-md)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--ink-faint)",
        fontFamily: "var(--font-mono)",
        fontSize: "12px",
        letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}
    >
      {name}
    </div>
  );
}
