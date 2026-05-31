// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./renderMarkdown.jsx";

function wrap(node) {
  return render(<div>{node}</div>);
}

describe("renderMarkdown", () => {
  it("renders a paragraph as a <p>", () => {
    const { container } = wrap(renderMarkdown("hello world", false));
    const ps = container.querySelectorAll("p");
    expect(ps.length).toBe(1);
    expect(ps[0].textContent).toBe("hello world");
  });

  it("renders **bold** as <strong>", () => {
    const { container } = wrap(renderMarkdown("hi **there** friend", false));
    const strong = container.querySelector("strong");
    expect(strong).toBeTruthy();
    expect(strong.textContent).toBe("there");
  });

  it("renders *italic* as <em>", () => {
    const { container } = wrap(renderMarkdown("see *italics* here", false));
    expect(container.querySelector("em").textContent).toBe("italics");
  });

  it("renders headings", () => {
    const { container } = wrap(
      renderMarkdown("# H1\n\n## H2\n\n### H3", false),
    );
    expect(container.querySelector("h1").textContent).toBe("H1");
    expect(container.querySelector("h2").textContent).toBe("H2");
    expect(container.querySelector("h3").textContent).toBe("H3");
  });

  it("renders ordered lists as <ol><li>", () => {
    const { container } = wrap(
      renderMarkdown("1. first\n2. second\n3. third", false),
    );
    const lis = container.querySelectorAll("ol > li");
    expect(lis.length).toBe(3);
    expect(lis[0].textContent).toBe("first");
    expect(lis[2].textContent).toBe("third");
  });

  it("renders unordered lists as <ul><li>", () => {
    const { container } = wrap(
      renderMarkdown("- one\n- two\n- three", false),
    );
    const lis = container.querySelectorAll("ul > li");
    expect(lis.length).toBe(3);
    expect(lis[0].textContent).toBe("one");
  });

  it("renders fenced code blocks as <pre><code>", () => {
    const md = "```python\nprint('hi')\n```";
    const { container } = wrap(renderMarkdown(md, false));
    const code = container.querySelector("pre code");
    expect(code).toBeTruthy();
    expect(code.textContent).toContain("print('hi')");
  });

  it("renders inline code as <code>", () => {
    const { container } = wrap(renderMarkdown("use `foo()` here", false));
    const code = container.querySelector("code");
    expect(code.textContent).toBe("foo()");
    // Inline code is not wrapped in <pre>.
    expect(container.querySelector("pre")).toBeNull();
  });

  it("renders links as <a> with the right href", () => {
    const { container } = wrap(
      renderMarkdown("[home](https://example.com)", false),
    );
    const a = container.querySelector("a");
    expect(a.textContent).toBe("home");
    expect(a.getAttribute("href")).toBe("https://example.com");
  });

  it("renders blockquotes as <blockquote>", () => {
    const { container } = wrap(renderMarkdown("> quoted text", false));
    expect(container.querySelector("blockquote")).toBeTruthy();
    expect(container.querySelector("blockquote").textContent).toContain(
      "quoted text",
    );
  });

  it("renders GFM tables", () => {
    const md = "| A | B |\n|---|---|\n| 1 | 2 |";
    const { container } = wrap(renderMarkdown(md, false));
    const headers = container.querySelectorAll("th");
    expect(headers.length).toBe(2);
    expect(headers[0].textContent).toBe("A");
    expect(container.querySelectorAll("td").length).toBe(2);
  });

  it("renders GFM strikethrough as <del>", () => {
    const { container } = wrap(renderMarkdown("see ~~old~~ note", false));
    expect(container.querySelector("del").textContent).toBe("old");
  });

  it("renders GFM task lists", () => {
    const md = "- [x] done\n- [ ] todo";
    const { container } = wrap(renderMarkdown(md, false));
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    expect(boxes.length).toBe(2);
    expect(boxes[0].checked).toBe(true);
    expect(boxes[1].checked).toBe(false);
  });

  it("appends a blinking .cursor span when streaming", () => {
    const { container } = wrap(renderMarkdown("partial", true));
    expect(container.querySelector(".cursor")).toBeTruthy();
  });

  it("omits the .cursor span when not streaming", () => {
    const { container } = wrap(renderMarkdown("done", false));
    expect(container.querySelector(".cursor")).toBeNull();
  });

  it("renders empty input without throwing", () => {
    const { container } = wrap(renderMarkdown("", false));
    // react-markdown produces no children for empty input.
    expect(container.textContent).toBe("");
  });

  it("drops raw HTML (security: react-markdown default-sanitizes)", () => {
    const { container } = wrap(
      renderMarkdown("<script>alert('xss')</script> safe", false),
    );
    expect(container.querySelector("script")).toBeNull();
  });
});
