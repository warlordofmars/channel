// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./renderMarkdown.jsx";

function wrap(nodes) {
  // renderMarkdown returns an array of <p>/<ol> elements — wrap them in a
  // <div> so testing-library can render the array.
  return render(<div>{nodes}</div>);
}

describe("renderMarkdown", () => {
  it("renders a single paragraph as a <p>", () => {
    const { container } = wrap(renderMarkdown("hello world", false));
    expect(container.querySelectorAll("p").length).toBe(1);
    expect(container.querySelector("p").textContent).toBe("hello world");
  });

  it("splits paragraphs on \\n\\n", () => {
    const { container } = wrap(renderMarkdown("one\n\ntwo\n\nthree", false));
    const ps = container.querySelectorAll("p");
    expect(ps.length).toBe(3);
    expect([ps[0].textContent, ps[1].textContent, ps[2].textContent]).toEqual(["one", "two", "three"]);
  });

  it("renders **bold** inline as <strong>", () => {
    const { container } = wrap(renderMarkdown("hi **there** friend", false));
    const strong = container.querySelector("strong");
    expect(strong).toBeTruthy();
    expect(strong.textContent).toBe("there");
    expect(container.querySelector("p").textContent).toBe("hi there friend");
  });

  it("renders an ordered-list block as <ol><li>", () => {
    const md = "1. first\n2. second\n3. third";
    const { container } = wrap(renderMarkdown(md, false));
    const ol = container.querySelector("ol");
    expect(ol).toBeTruthy();
    const lis = ol.querySelectorAll("li");
    expect(lis.length).toBe(3);
    expect(lis[0].textContent).toBe("first");
    expect(lis[2].textContent).toBe("third");
  });

  it("renders bold inside list items", () => {
    const { container } = wrap(renderMarkdown("1. **batch** writes", false));
    expect(container.querySelector("ol li strong").textContent).toBe("batch");
  });

  it("appends a blinking .cursor span to the last paragraph only when streaming", () => {
    const { container: streaming } = wrap(renderMarkdown("one\n\ntwo", true));
    const ps = streaming.querySelectorAll("p");
    expect(ps[0].querySelector(".cursor")).toBeNull();
    expect(ps[1].querySelector(".cursor")).toBeTruthy();

    const { container: done } = wrap(renderMarkdown("one\n\ntwo", false));
    expect(done.querySelector(".cursor")).toBeNull();
  });

  it("does not append the cursor inside an ordered-list block", () => {
    // The last block is an OL — streaming cursor only attaches to <p>.
    const { container } = wrap(renderMarkdown("intro\n\n1. one\n2. two", true));
    expect(container.querySelector(".cursor")).toBeNull();
  });

  it("renders an empty string as a single empty paragraph", () => {
    const { container } = wrap(renderMarkdown("", false));
    expect(container.querySelectorAll("p").length).toBe(1);
    expect(container.querySelector("p").textContent).toBe("");
  });
});
