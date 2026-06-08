// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen, fireEvent } from "@testing-library/react";
import ToolResultBlock from "./ToolResultBlock.jsx";

describe("ToolResultBlock — default branch", () => {
  it("renders the default branch when kind is unrecognised", () => {
    render(<ToolResultBlock kind="unknown" summary="hello" />);
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("renders summary text by default (no kind specified)", () => {
    render(<ToolResultBlock summary="the answer is 4" />);
    expect(screen.getByText("the answer is 4")).toBeInTheDocument();
  });

  it("tags the wrapper with data-kind for future-branch dispatch", () => {
    const { container } = render(<ToolResultBlock kind="custom" summary="x" />);
    expect(container.firstChild).toHaveAttribute("data-kind", "custom");
  });

  it("falls back to data-kind=default when kind is omitted", () => {
    const { container } = render(<ToolResultBlock summary="y" />);
    expect(container.firstChild).toHaveAttribute("data-kind", "default");
  });
});

describe("ToolResultBlock — code-output branch", () => {
  const basePayload = {
    stdout: "42\n",
    stderr: "",
    exit_code: 0,
    duration_ms: 12,
    truncated: false,
    timed_out: false,
    images: [],
  };

  it("renders stdout in a pre", () => {
    render(<ToolResultBlock kind="code-output" payload={basePayload} />);
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("shows meta line with exit code and duration", () => {
    render(<ToolResultBlock kind="code-output" payload={basePayload} />);
    expect(screen.getByText(/exit 0/)).toBeInTheDocument();
    expect(screen.getByText(/12ms/)).toBeInTheDocument();
  });

  it("collapses stdout when > 5 lines and exposes a 'Show all' button", () => {
    const manyLines = Array.from({ length: 12 }, (_, i) => `line ${i}`).join("\n");
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, stdout: manyLines }}
      />,
    );
    const toggle = screen.getByText(/Show all 12 lines/);
    expect(toggle).toBeInTheDocument();
    expect(screen.getByText(/line 0/)).toBeInTheDocument();
    expect(screen.queryByText(/line 8/)).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText(/line 8/)).toBeInTheDocument();
  });

  it("does not show the toggle for 5-or-fewer lines of stdout", () => {
    render(<ToolResultBlock kind="code-output" payload={basePayload} />);
    expect(screen.queryByText(/Show all/)).not.toBeInTheDocument();
  });

  it("renders stderr inside a <details> when present", () => {
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, stderr: "Traceback: ..." }}
      />,
    );
    expect(screen.getByText(/stderr.*bytes/)).toBeInTheDocument();
  });

  it("omits the stderr <details> when stderr is empty", () => {
    render(<ToolResultBlock kind="code-output" payload={basePayload} />);
    expect(screen.queryByText(/stderr.*bytes/)).not.toBeInTheDocument();
  });

  it("renders each image as an <img> with a data URI", () => {
    const payload = {
      ...basePayload,
      images: [
        { mime: "image/png", b64: "AAA=" },
        { mime: "image/jpeg", b64: "BBB=" },
      ],
    };
    const { container } = render(
      <ToolResultBlock kind="code-output" payload={payload} />,
    );
    const imgs = container.querySelectorAll("img");
    expect(imgs).toHaveLength(2);
    expect(imgs[0].src).toBe("data:image/png;base64,AAA=");
    expect(imgs[1].src).toBe("data:image/jpeg;base64,BBB=");
  });

  it("shows the truncation flag in the meta line", () => {
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, truncated: true }}
      />,
    );
    expect(screen.getByText(/output truncated/)).toBeInTheDocument();
  });

  it("shows the timeout flag in the meta line", () => {
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, timed_out: true }}
      />,
    );
    expect(screen.getByText(/timed out at 270s/)).toBeInTheDocument();
  });

  it("handles missing payload gracefully (no crash)", () => {
    const { container } = render(<ToolResultBlock kind="code-output" />);
    expect(container.firstChild).toHaveAttribute("data-kind", "code-output");
  });

  it("handles payload with no stdout field (renders meta without stdout pane)", () => {
    const payload = {
      stderr: "",
      exit_code: 0,
      duration_ms: 5,
      truncated: false,
      timed_out: false,
      images: [],
    };
    const { container } = render(
      <ToolResultBlock kind="code-output" payload={payload} />,
    );
    expect(container.firstChild).toHaveAttribute("data-kind", "code-output");
    expect(screen.getByText(/exit 0/)).toBeInTheDocument();
  });

  it("shows 'Collapse' label after expanding collapsed stdout", () => {
    const manyLines = Array.from({ length: 12 }, (_, i) => `line ${i}`).join("\n");
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, stdout: manyLines }}
      />,
    );
    const toggle = screen.getByText(/Show all 12 lines/);
    fireEvent.click(toggle);
    expect(screen.getByText("Collapse")).toBeInTheDocument();
  });
});
