// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * Marketing image. When `src` is provided, renders a real <img>; otherwise
 * renders a placeholder block tagged `data-image-slot="<name>"` so a future
 * content pass can find every empty slot.
 *
 * Use:
 *   <ImageSlot name="hero-screen-light" src="/screens/app-02-home.png" alt="…" />
 *   <ImageSlot name="blog-cover-1" aspect="3 / 2" className="rounded-lg" />
 */
export default function ImageSlot({ name, aspect = "16 / 9", className = "", src, alt = "" }) {
  if (src) {
    return (
      <img
        className={`image-slot ${className}`.trim()}
        data-image-slot={name}
        src={src}
        alt={alt}
        style={{ aspectRatio: aspect, objectFit: "cover", borderRadius: "var(--r-md)" }}
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
