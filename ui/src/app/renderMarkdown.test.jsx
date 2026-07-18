// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderMarkdown, scanFences } from "./renderMarkdown.jsx";

function wrap(node) {
  return render(<div>{node}</div>);
}

function codeAsset(fenceIndex, overrides = {}) {
  return {
    asset_id: `as-${fenceIndex}`,
    kind: "code",
    title: `snippet-${fenceIndex}.py`,
    size_bytes: 400,
    source: { fence_index: fenceIndex, lang: "python" },
    ...overrides,
  };
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

  it("renders a single \\n as a <br> line break (remark-breaks)", () => {
    const { container } = wrap(renderMarkdown("line one\nline two", false));
    // Both lines stay in one paragraph, separated by a hard break.
    const ps = container.querySelectorAll("p");
    expect(ps.length).toBe(1);
    expect(ps[0].querySelector("br")).toBeTruthy();
    expect(ps[0].textContent).toContain("line one");
    expect(ps[0].textContent).toContain("line two");
  });

  it("still renders \\n\\n as separate <p> paragraphs", () => {
    const { container } = wrap(renderMarkdown("para one\n\npara two", false));
    const ps = container.querySelectorAll("p");
    expect(ps.length).toBe(2);
    expect(ps[0].textContent).toBe("para one");
    expect(ps[1].textContent).toBe("para two");
    // No <br> injected — paragraph breaks are unchanged by remark-breaks.
    expect(container.querySelector("br")).toBeNull();
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

describe("scanFences", () => {
  it("enumerates a single fenced block with its char span", () => {
    const text = "before\n```python\nx = 1\n```\nafter";
    const fences = scanFences(text);
    expect(fences).toHaveLength(1);
    expect(fences[0].fenceIndex).toBe(0);
    expect(text.slice(fences[0].start, fences[0].end)).toBe(
      "```python\nx = 1\n```",
    );
  });

  it("counts every fence, mermaid and short included, in order", () => {
    const text = "```mermaid\ngraph TD\n```\n\ntext\n\n```js\na()\n```";
    const fences = scanFences(text);
    expect(fences.map((f) => f.fenceIndex)).toEqual([0, 1]);
  });

  it("captures an unterminated fence through end-of-text", () => {
    const text = "intro\n```\nno closer here\nstill code";
    const fences = scanFences(text);
    expect(fences).toHaveLength(1);
    expect(text.slice(fences[0].start, fences[0].end)).toBe(
      "```\nno closer here\nstill code",
    );
  });

  it("does not treat fence-like lines inside a body as new openers", () => {
    const text = "````\n```\ninner\n```\n````";
    const fences = scanFences(text);
    // The 4-backtick opener only closes on a 4+ backtick line, so the
    // inner 3-backtick lines are body, not a second fence.
    expect(fences).toHaveLength(1);
  });

  it("returns no fences for plain text", () => {
    expect(scanFences("just prose\nover two lines")).toEqual([]);
  });
});

describe("renderMarkdown — fence-decorate (#362, reconciles #327)", () => {
  it("renders the matching fence as native code + an open-in-panel affordance (no card)", () => {
    const md = "```python\nx = 1\ny = 2\n```";
    const codeAssets = new Map([[0, codeAsset(0)]]);
    const { container, getByText } = wrap(
      renderMarkdown(md, false, { codeAssets, onOpenAsset: vi.fn() }),
    );
    // The fence renders as its OWN code block — not swapped to a card.
    const code = container.querySelector(".code-decorated pre code");
    expect(code).toBeTruthy();
    expect(code.textContent).toContain("x = 1");
    expect(code.textContent).toContain("y = 2");
    // No AssetCard, and no card title text.
    expect(container.querySelector(".art-inline")).toBeNull();
    // The open-in-panel affordance is present and reachable.
    expect(getByText("Open in panel")).toBeTruthy();
    expect(container.querySelector(".code-open-panel")).toBeTruthy();
  });

  it("hides the decorative affordance icon from assistive tech (aria-hidden)", () => {
    const md = "```python\nx = 1\n```";
    const { container } = wrap(
      renderMarkdown(md, false, {
        codeAssets: new Map([[0, codeAsset(0)]]),
        onOpenAsset: vi.fn(),
      }),
    );
    const iconWrap = container.querySelector(
      ".code-open-panel .code-open-panel-ic",
    );
    expect(iconWrap).toBeTruthy();
    expect(iconWrap.getAttribute("aria-hidden")).toBe("true");
  });

  it("renders exactly one code block — no double-render of the fence", () => {
    const md = "```python\nx = 1\ny = 2\n```";
    const codeAssets = new Map([[0, codeAsset(0)]]);
    const { container } = wrap(
      renderMarkdown(md, false, { codeAssets, onOpenAsset: vi.fn() }),
    );
    expect(container.querySelectorAll("pre code").length).toBe(1);
    expect(container.querySelectorAll(".code-open-panel").length).toBe(1);
  });

  it("preserves the fence text byte-for-byte in the decorated block", () => {
    const md = "```python\ndef f(x):\n    return x * 2\n```";
    const codeAssets = new Map([[0, codeAsset(0)]]);
    const { container } = wrap(
      renderMarkdown(md, false, { codeAssets, onOpenAsset: vi.fn() }),
    );
    // react-markdown appends a trailing newline to the code block; the
    // author's exact source (incl. the indented body) is otherwise intact.
    const code = container.querySelector(".code-decorated pre code");
    expect(code.textContent).toBe("def f(x):\n    return x * 2\n");
  });

  it("keeps surrounding prose (pre + tail segments) around the decorated fence", () => {
    const md = "intro text\n\n```python\nx = 1\ny = 2\n```\n\noutro text";
    const codeAssets = new Map([[0, codeAsset(0)]]);
    const { container, getByText } = wrap(
      renderMarkdown(md, false, { codeAssets, onOpenAsset: vi.fn() }),
    );
    expect(getByText("intro text")).toBeTruthy();
    expect(getByText("outro text")).toBeTruthy();
    expect(container.querySelector(".code-decorated pre code")).toBeTruthy();
    expect(container.querySelector(".art-inline")).toBeNull();
  });

  it("leaves a non-matching fence as plain code with no affordance (no locatable ordinal)", () => {
    const md = "```python\nx = 1\n```";
    const codeAssets = new Map([[5, codeAsset(5)]]);
    const { container } = wrap(
      renderMarkdown(md, false, { codeAssets, onOpenAsset: vi.fn() }),
    );
    expect(container.querySelector(".art-inline")).toBeNull();
    expect(container.querySelector(".code-decorated")).toBeNull();
    expect(container.querySelector(".code-open-panel")).toBeNull();
    // The code is still there — degraded to a plain single-block render.
    expect(container.querySelector("pre code")).toBeTruthy();
  });

  it("decorates only the matching fence and leaves the other as plain code", () => {
    const md = "```js\na()\n```\n\nmid\n\n```py\nb = 2\n```";
    const codeAssets = new Map([[1, codeAsset(1)]]);
    const { container } = wrap(
      renderMarkdown(md, false, { codeAssets, onOpenAsset: vi.fn() }),
    );
    // Both fences render as code; only fence 1 carries the affordance.
    const blocks = container.querySelectorAll("pre code");
    expect(blocks.length).toBe(2);
    expect(container.querySelectorAll(".code-open-panel").length).toBe(1);
    // The decorated block is fence 1 (b = 2), not fence 0 (a()).
    expect(
      container.querySelector(".code-decorated pre code").textContent,
    ).toContain("b = 2");
  });

  it("forwards clicks on the affordance to onOpenAsset", () => {
    const md = "```python\nx = 1\n```";
    const asset = codeAsset(0);
    const onOpenAsset = vi.fn();
    const { getByText } = wrap(
      renderMarkdown(md, false, {
        codeAssets: new Map([[0, asset]]),
        onOpenAsset,
      }),
    );
    fireEvent.click(getByText("Open in panel"));
    expect(onOpenAsset).toHaveBeenCalledWith(asset);
  });

  it("tolerates a missing onOpenAsset when the affordance is clicked", () => {
    const md = "```python\nx = 1\n```";
    const { getByText } = wrap(
      renderMarkdown(md, false, { codeAssets: new Map([[0, codeAsset(0)]]) }),
    );
    // No onOpenAsset supplied — the optional-chain call must not throw.
    expect(() => fireEvent.click(getByText("Open in panel"))).not.toThrow();
  });

  it("appends the streaming cursor in the decorate path too", () => {
    const md = "```python\nx = 1\n```";
    const { container } = wrap(
      renderMarkdown(md, true, {
        codeAssets: new Map([[0, codeAsset(0)]]),
        onOpenAsset: vi.fn(),
      }),
    );
    expect(container.querySelector(".code-decorated pre code")).toBeTruthy();
    expect(container.querySelector(".cursor")).toBeTruthy();
  });

  it("takes the fast path when the codeAssets map is empty", () => {
    const md = "```python\nx = 1\n```";
    const { container } = wrap(
      renderMarkdown(md, false, { codeAssets: new Map() }),
    );
    // Empty map → unchanged single-ReactMarkdown render.
    expect(container.querySelector("pre code")).toBeTruthy();
    expect(container.querySelector(".code-decorated")).toBeNull();
    expect(container.querySelector(".art-inline")).toBeNull();
  });
});
