// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles/channel.css";
import "./styles/site.css";
import "./styles/app.css";

// Mark the document for platform-aware styles. window.channelDesktop is
// only present in the Electron renderer; on the web SPA neither class is
// applied. The class names are consumed by app.css to (e.g.) push the
// sidebar header right of macOS traffic lights.
if (typeof window !== "undefined" && window.channelDesktop) {
  document.documentElement.classList.add("is-electron");
  if (window.channelDesktop.platform === "darwin") {
    document.documentElement.classList.add("is-electron-mac");
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
