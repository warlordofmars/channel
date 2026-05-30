// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

// React's error-boundary contract still requires a class component
// — there is no hook equivalent for `componentDidCatch` /
// `getDerivedStateFromError`. Wrap the entire route tree so a thrown
// exception in any rendered component falls into a friendly
// "Something went wrong" page with a reload button instead of
// blanking the whole tab.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: "" };
    this.handleReload = this.handleReload.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, message: error?.message ?? "" };
  }

  componentDidCatch(error, info) {
    // Best-effort: log to the browser console so a developer
    // inspecting the page can find the trace. A real backend
    // ingest endpoint can be wired here later.
    if (globalThis.console) {
      // eslint-disable-next-line no-console
      globalThis.console.error("ErrorBoundary caught:", error, info);
    }
  }

  handleReload() {
    if (globalThis.location) globalThis.location.reload();
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div
        role="alert"
        data-testid="error-boundary"
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--canvas)",
          color: "var(--ink)",
          fontFamily: "var(--font-sans)",
          padding: "24px",
        }}
      >
        <div
          style={{
            background: "var(--raised)",
            border: "1px solid var(--border)",
            borderRadius: "var(--r-lg)",
            boxShadow: "var(--shadow-md)",
            padding: "32px",
            maxWidth: "480px",
            width: "100%",
            textAlign: "center",
          }}
        >
          <h1 style={{ fontSize: "26px", fontWeight: 600, marginBottom: "12px" }}>
            Something went wrong
          </h1>
          <p style={{ color: "var(--ink-soft)", marginBottom: "20px" }}>
            {this.state.message || "An unexpected error occurred."}
          </p>
          <button
            onClick={this.handleReload}
            style={{
              background: "var(--accent)",
              color: "var(--on-accent)",
              border: "none",
              borderRadius: "var(--r-md)",
              padding: "10px 18px",
              fontSize: "14px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
