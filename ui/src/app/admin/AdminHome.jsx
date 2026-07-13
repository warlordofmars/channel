// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import Icon from "../../components/Icon.jsx";

/**
 * `/app/admin` index page — two-card landing for the admin area.
 *
 * Deliberately a thin container: sub-issue #238 (users list/detail)
 * and #239 (dashboard) mount the real views at the routes these cards
 * point at; this page stays the stable entry point. Cards reuse the
 * `.grid`/`.card` shapes from Projects.jsx so the admin area inherits
 * the app look without new CSS.
 */
export default function AdminHome() {
  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head">
          <h2>Admin</h2>
          <p>Operational visibility — who is using Channel and how.</p>
        </div>
        <div className="grid">
          <Link
            className="card"
            to="/app/admin/users"
            style={{ textDecoration: "none" }}
          >
            <div className="ct">
              <span
                className="badge"
                style={{ background: "var(--accent-soft)", color: "var(--accent-ink)" }}
              >
                <Icon name="cowork" size={19} />
              </span>
              <h3>Users</h3>
            </div>
            <p className="desc">
              Registered users — who they are, when they joined, and signs of life.
            </p>
          </Link>
          <Link
            className="card"
            to="/app/admin/dashboard"
            style={{ textDecoration: "none" }}
          >
            <div className="ct">
              <span
                className="badge"
                style={{ background: "var(--accent-soft)", color: "var(--accent-ink)" }}
              >
                <Icon name="wave" size={19} />
              </span>
              <h3>Dashboard</h3>
            </div>
            <p className="desc">
              Usage rollups — chat volume, tool calls, and growth over time.
            </p>
          </Link>
        </div>
      </div>
    </div>
  );
}
