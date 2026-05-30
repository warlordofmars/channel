// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { useSearchParams } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import ArtifactPanel from "./ArtifactPanel.jsx";
import { ARTIFACTS } from "../data.js";
import { artIcon } from "./artifactHelpers.js";

const ARTIFACT_PARAM = "artifact";

/**
 * `/app/artifacts` view. Translated from design-sources/app/views.jsx
 * `ArtifactsView`. The detail panel is gated on a `?artifact=<id>`
 * search param so the URL is bookmarkable — refresh keeps it open.
 * Per the design spec §URL state, this is the only stateful piece of
 * the route.
 */
export default function Artifacts() {
  const [params, setParams] = useSearchParams();
  const openId = params.get(ARTIFACT_PARAM);
  const openArtifact = openId ? ARTIFACTS.find((a) => a.id === openId) ?? null : null;

  function openRow(a) {
    const next = new URLSearchParams(params);
    next.set(ARTIFACT_PARAM, a.id);
    setParams(next);
  }
  function closePanel() {
    const next = new URLSearchParams(params);
    next.delete(ARTIFACT_PARAM);
    setParams(next);
  }

  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head">
          <h2>Artifacts</h2>
          <p>Documents, code, and interactive pieces Channel has built with you.</p>
        </div>
        {ARTIFACTS.map((a) => (
          <div className="list-row" key={a.id} onClick={() => openRow(a)}>
            <span className="badge"><Icon name={artIcon(a.kind)} size={18} /></span>
            <div className="info">
              <div className="ti">{a.title}</div>
              <div className="sub">
                {a.lines} · updated {a.updated}{a.project ? " · " + a.project : ""}
              </div>
            </div>
            <span className="kind">{a.kind}</span>
          </div>
        ))}
      </div>
      <ArtifactPanel artifact={openArtifact} onClose={closePanel} />
    </div>
  );
}
