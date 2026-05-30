// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import { PROJECTS } from "../data.js";
import { colorFor, inkFor } from "./artifactHelpers.js";

/**
 * `/app/projects` view. Translated from design-sources/app/views.jsx
 * `ProjectsView`. Grid of project cards plus a "New project" tile (the
 * tile is a Phase 6e no-op; later phases wire the create-project modal).
 */
export default function Projects() {
  const navigate = useNavigate();
  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head">
          <h2>Projects</h2>
          <p>Group related chats, share knowledge, and keep context in one place.</p>
        </div>
        <div className="grid">
          <button type="button" className="card new-card">
            <Icon name="plus" size={20} /> New project
          </button>
          {PROJECTS.map((p) => (
            <div
              className="card"
              key={p.id}
              onClick={() => navigate(`/app/projects/${p.id}`)}
            >
              <div className="ct">
                <span
                  className="badge"
                  style={{ background: colorFor(p.color), color: inkFor(p.color) }}
                >
                  <Icon name="projects" size={19} />
                </span>
                <h3>{p.name}</h3>
              </div>
              <p className="desc">{p.desc}</p>
              <div className="meta">
                <span><Icon name="chat" size={14} /> {p.chats} chats</span>
                <span><Icon name="doc" size={14} /> {p.docs} docs</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
