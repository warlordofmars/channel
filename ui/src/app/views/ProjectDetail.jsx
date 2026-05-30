// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { useNavigate, useParams } from "react-router-dom";
import Composer from "../Composer.jsx";
import Icon from "../../components/Icon.jsx";
import { useChannelPrefs } from "../../hooks/useChannelPrefs.js";
import { MODELS, PROJECTS, PROJECT_DOCS, RECENTS } from "../data.js";
import { colorFor, inkFor } from "./artifactHelpers.js";

const PENDING_KEY = "channel-pending-send";

/**
 * `/app/projects/:id` view. Translated from design-sources/app/views.jsx
 * `ProjectDetail`. Composer follows the same stash-and-navigate pattern
 * as ChatHome — Phase 6e doesn't scope the conversation to the project
 * (no backend persistence yet); we just kick off /app/c/new with the
 * user's draft. Recents are the first 6 mock entries — clicking one
 * opens its canned conversation.
 */
export default function ProjectDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const prefs = useChannelPrefs();
  const project = PROJECTS.find((p) => p.id === id);

  if (!project) {
    return (
      <div className="view">
        <div className="view-inner">
          <div className="view-head">
            <h2>Project not found</h2>
            <p>The link may be stale or the project was renamed.</p>
          </div>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => navigate("/app/projects")}
          >
            Back to projects
          </button>
        </div>
      </div>
    );
  }

  const chats = RECENTS.slice(0, 6);
  const modelObj = MODELS.find((m) => m.id === prefs.model) ?? MODELS[0];
  const setModelObj = (m) => prefs.setModel(m.id);

  function startChat(text, atts) {
    try {
      sessionStorage.setItem(
        PENDING_KEY,
        JSON.stringify({
          text,
          atts,
          modelId: modelObj.id,
          effort: prefs.effort,
        })
      );
    } catch {
      /* private mode etc — Conversation just renders empty */
    }
    navigate("/app/c/new");
  }

  return (
    <div className="view">
      <div className="view-inner" style={{ maxWidth: 820 }}>
        <button
          type="button"
          className="back-btn"
          onClick={() => navigate("/app/projects")}
        >
          <span style={{ transform: "rotate(180deg)", display: "inline-flex" }}>
            <Icon name="chevron-right" size={16} />
          </span>
          Projects
        </button>
        <div className="proj-head">
          <span
            className="badge"
            style={{ background: colorFor(project.color), color: inkFor(project.color) }}
          >
            <Icon name="projects" size={24} />
          </span>
          <div style={{ flex: 1 }}>
            <h2>{project.name}</h2>
            <p>{project.desc}</p>
          </div>
        </div>

        <Composer
          model={modelObj}
          effort={prefs.effort}
          setModel={setModelObj}
          setEffort={prefs.setEffort}
          onSend={startChat}
          placeholder={`New chat in ${project.name}…`}
        />

        <div className="proj-section">
          <div className="proj-section-h">
            <h3>Project knowledge</h3>
            <button type="button" className="ghost-sm">
              <Icon name="plus" size={15} /> Add
            </button>
          </div>
          <div className="doc-row">
            {PROJECT_DOCS.map((d) => (
              <div className="doc-chip" key={d}>
                <span className="dc-ic"><Icon name="doc" size={15} /></span>{d}
              </div>
            ))}
          </div>
        </div>

        <div className="proj-section">
          <div className="proj-section-h">
            <h3>Chats in this project</h3>
          </div>
          {chats.map((c) => (
            <button
              type="button"
              className="proj-chat"
              key={c.id}
              onClick={() => navigate(`/app/c/${c.id}`)}
            >
              <span className="pc-ic"><Icon name="chat" size={16} /></span>
              <span className="pc-t">{c.title}</span>
              <span className="pc-go"><Icon name="chevron-right" size={16} /></span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
