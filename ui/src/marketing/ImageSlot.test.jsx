// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ImageSlot from "./ImageSlot.jsx";

describe("ImageSlot", () => {
  it("renders a div with the data-image-slot attribute set to name", () => {
    const { container } = render(<ImageSlot name="hero-shot" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot).toBeTruthy();
    expect(slot.getAttribute("data-image-slot")).toBe("hero-shot");
  });

  it("applies a default aspect ratio of 16/9 when not specified", () => {
    const { container } = render(<ImageSlot name="x" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot.style.aspectRatio).toBe("16 / 9");
  });

  it("accepts a custom aspect prop and applies it as aspectRatio style", () => {
    const { container } = render(<ImageSlot name="square" aspect="1 / 1" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot.style.aspectRatio).toBe("1 / 1");
  });

  it("merges a className prop with the default 'image-slot' class", () => {
    const { container } = render(<ImageSlot name="x" className="hero-shot" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot.className).toContain("image-slot");
    expect(slot.className).toContain("hero-shot");
  });

  it("renders the slot name as a label inside the placeholder", () => {
    const { container } = render(<ImageSlot name="team-photo" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot.textContent).toContain("team-photo");
  });

  it("renders an <img> when src is provided, with alt + data-image-slot", () => {
    const { container } = render(
      <ImageSlot name="hero" src="/screens/x.png" alt="hero alt" aspect="4 / 3" className="hero-shot" />
    );
    const img = container.querySelector("img[data-image-slot]");
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe("/screens/x.png");
    expect(img.getAttribute("alt")).toBe("hero alt");
    expect(img.getAttribute("data-image-slot")).toBe("hero");
    expect(img.style.aspectRatio).toBe("4 / 3");
    expect(img.className).toContain("hero-shot");
    // The placeholder block is NOT also rendered.
    expect(container.querySelector("div[data-image-slot]")).toBeNull();
  });

  it("defaults alt to empty string when src is provided without alt", () => {
    const { container } = render(<ImageSlot name="x" src="/y.png" />);
    const img = container.querySelector("img[data-image-slot]");
    expect(img.getAttribute("alt")).toBe("");
  });
});
