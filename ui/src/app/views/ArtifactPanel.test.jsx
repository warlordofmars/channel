// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ArtifactPanel from "./ArtifactPanel.jsx";
import { ARTIFACTS } from "../data.js";

function byKind(kind) {
  return ARTIFACTS.find((a) => a.kind === kind);
}

describe("ArtifactPanel", () => {
  it("renders nothing when artifact is null", () => {
    const { container } = render(<ArtifactPanel artifact={null} onClose={() => {}} />);
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("renders title + kind in the header for a Code artifact", () => {
    const a = byKind("Code");
    render(<ArtifactPanel artifact={a} onClose={() => {}} />);
    expect(screen.getByText(a.title)).toBeTruthy();
    expect(screen.getByText(/Code/)).toBeTruthy();
    expect(screen.getByText(/142 lines/)).toBeTruthy();
  });

  it("omits the trailing ' · <lines>' when artifact.lines is the placeholder '—'", () => {
    const a = byKind("Document");
    render(<ArtifactPanel artifact={a} onClose={() => {}} />);
    // The kind text should be present, but no " · —" suffix.
    const sub = document.body.querySelector(".art-phead .s");
    expect(sub).toBeTruthy();
    expect(sub.textContent).toBe("Document");
  });

  it("renders <pre><code> for kind=Code", () => {
    render(<ArtifactPanel artifact={byKind("Code")} onClose={() => {}} />);
    expect(document.body.querySelector("pre.art-code code")).toBeTruthy();
  });

  it("renders an SVG chart for kind=Chart", () => {
    render(<ArtifactPanel artifact={byKind("Chart")} onClose={() => {}} />);
    const svg = document.body.querySelector(".art-chart svg");
    expect(svg).toBeTruthy();
    // 7 bars + one baseline line.
    expect(svg.querySelectorAll("rect").length).toBe(7);
    expect(svg.querySelectorAll("line").length).toBe(1);
  });

  it("renders an art-table for kind=Data with a header row + 4 data rows", () => {
    render(<ArtifactPanel artifact={byKind("Data")} onClose={() => {}} />);
    const table = document.body.querySelector("table.art-table");
    expect(table).toBeTruthy();
    expect(table.querySelectorAll("thead th").length).toBe(4);
    expect(table.querySelectorAll("tbody tr").length).toBe(4);
  });

  it("renders the interactive preview block for kind=Interactive", () => {
    render(<ArtifactPanel artifact={byKind("Interactive")} onClose={() => {}} />);
    expect(document.body.querySelector(".art-interactive")).toBeTruthy();
    expect(screen.getByText(/Interactive preview/i)).toBeTruthy();
  });

  it("renders the document body for kind=Document (default branch)", () => {
    render(<ArtifactPanel artifact={byKind("Document")} onClose={() => {}} />);
    expect(document.body.querySelector(".art-doc")).toBeTruthy();
    expect(screen.getByText(/Summary/i)).toBeTruthy();
  });

  it("clicking the close button fires onClose", () => {
    const onClose = vi.fn();
    render(<ArtifactPanel artifact={byKind("Code")} onClose={onClose} />);
    fireEvent.click(screen.getByTitle("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking the backdrop fires onClose", () => {
    const onClose = vi.fn();
    const { container } = render(<ArtifactPanel artifact={byKind("Code")} onClose={onClose} />);
    fireEvent.click(container.querySelector(".art-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Copy + Download buttons render but are Phase-6e no-ops", () => {
    render(<ArtifactPanel artifact={byKind("Code")} onClose={() => {}} />);
    for (const title of ["Copy", "Download"]) {
      expect(() => fireEvent.click(screen.getByTitle(title))).not.toThrow();
    }
  });

  it("falls through to the Document renderer for an unknown kind", () => {
    render(
      <ArtifactPanel
        artifact={{ id: "x", title: "Mystery", kind: "Mystery", updated: "", lines: "—", project: null }}
        onClose={() => {}}
      />
    );
    expect(document.body.querySelector(".art-doc")).toBeTruthy();
  });
});
