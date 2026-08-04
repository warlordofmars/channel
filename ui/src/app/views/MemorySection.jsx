// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import { listMemoryRecords } from "../../api.js";
import { relativeTime } from "./artifactHelpers.js";

// The `?chat_id=` deep link that scopes the panel to one chat. Named to
// match the API query parameter so a copied URL works in both places.
const CHAT_PARAM = "chat_id";

// Chats per page — the endpoint's `limit` counts chats, not records.
const PAGE_LIMIT = 10;

// Provenance classes, keyed by `classify_kind`'s return values in
// `src/channel/agents/memory_records.py` (epic #129 decision 1). An
// unrecognised kind falls through to its raw value rather than being
// hidden: a record Channel is storing must never be invisible here.
const KIND_LABELS = {
  remembered: "Remembered",
  meta: "Tool note",
  conversation: "From the conversation",
};

// Filter chips. `all` is the default; the rest mirror KIND_LABELS' keys.
const KIND_FILTERS = [
  ["all", "All"],
  ["remembered", "Remembered"],
  ["meta", "Tool notes"],
  ["conversation", "Conversation"],
];

function kindLabel(kind) {
  return KIND_LABELS[kind] ?? kind;
}

// AgentCore's conversational roles. Rendered in the app's own vocabulary so
// the panel reads as prose rather than as a wire dump.
function roleLabel(role) {
  if (role === "USER") return "You";
  if (role === "ASSISTANT") return "Channel";
  return role;
}

// Chat title, falling back to the raw chat id. A chat can legitimately have
// no title (auto-titling is a kill-switched post-stream step), and an
// untitled group still has to be identifiable.
function chatLabel(group) {
  return group.chat_title || group.chat_id;
}

function plural(count, word) {
  return count === 1 ? word : `${word}s`;
}

/**
 * The stored-vs-used sentence, built ENTIRELY from the API's
 * `recall_window` envelope — never from hardcoded numbers (epic #129
 * decision 2).
 *
 * Channel stores far more than it uses: #227 measured the injected recall
 * block at ~366 tokens, byte-identical for a 312-message chat and a
 * 56-message one. Showing only stored records implies Channel is using all
 * of it; showing only the recalled slice implies it has forgotten the rest.
 * Both are silent lies in opposite directions, so the panel states the
 * window in the same breath as the list.
 *
 * The numbers come from `recall.py`'s own constants via the API, so the
 * sentence tracks the caps automatically the first time they move.
 */
function recallSentence(window) {
  if (!window.enabled) {
    return (
      "Channel stores everything below. Recall is switched off right now, " +
      "so none of it is being loaded into new chats."
    );
  }
  return (
    "Channel stores everything below. When you start a new chat it loads a " +
    `short extract — up to ${window.events_per_session} ` +
    `${plural(window.events_per_session, "turn")} from each of your ` +
    `${window.max_sessions} most recent ${plural(window.max_sessions, "chat")}, ` +
    `each trimmed to ${window.text_truncate} characters — ordered by ` +
    `${window.ordering}, not by relevance.`
  );
}

/**
 * "What Channel remembers" — the read-only half of the memory control panel
 * (#479, epic #129), rendered as a section inside `/app/customize`.
 *
 * Sourced from `GET /api/memory/records` (#475): chats newest-first, each
 * chat's records oldest-first, plus the #245 rolling head summaries as a
 * separate read-only group (decision 4 — they are derived and regenerate
 * whenever the history window slides, so a delete affordance would silently
 * reappear).
 *
 * Records inside the recall window carry a badge; records outside it render
 * normally rather than greyed out. They are still stored, which is the whole
 * point of being able to see them.
 *
 * Destructive and editing affordances, and export, are deliberately absent —
 * they are the next sub-issue.
 */
export default function MemorySection() {
  const [searchParams, setSearchParams] = useSearchParams();
  const chatFilter = searchParams.get(CHAT_PARAM);

  const [groups, setGroups] = useState([]);
  const [summaries, setSummaries] = useState([]);
  const [recall, setRecall] = useState(null);
  const [withheld, setWithheld] = useState(0);
  const [cursor, setCursor] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [loadingMore, setLoadingMore] = useState(false);
  const [kindFilter, setKindFilter] = useState("all");

  // `loadMore` lives outside an effect and so has no cleanup — these two
  // refs are what stand in for one. `mountedRef` drops a page that lands
  // after the user navigates away; `epochRef` drops one that belongs to a
  // list that no longer exists, which is the narrower race: clicking "Load
  // more" and then clearing the chat scope within the same round trip would
  // otherwise concatenate the old scope's page onto the new one.
  //
  // `loadFirstPage` needs neither — React runs its cleanup on an unmount AND
  // on a `chatFilter` change, so the `cancelled` closure below covers both.
  const mountedRef = useRef(true);
  const epochRef = useRef(0);
  useEffect(function trackMounted() {
    mountedRef.current = true;
    return function markUnmounted() {
      mountedRef.current = false;
    };
  }, []);

  useEffect(
    function loadFirstPage() {
      let cancelled = false;
      epochRef.current += 1;
      setStatus("loading");
      // A `loadMore` in flight for the previous scope will be dropped by the
      // epoch check, so nothing else will clear its pending flag.
      setLoadingMore(false);
      listMemoryRecords({ limit: PAGE_LIMIT, chatId: chatFilter })
        .then(function onFirstPage(page) {
          if (cancelled) return;
          setGroups(page.groups);
          setSummaries(page.summaries);
          setRecall(page.recall_window);
          setWithheld(page.withheld_record_count);
          setCursor(page.next_cursor);
          setStatus("ready");
        })
        .catch(function onFirstPageError() {
          if (!cancelled) setStatus("error");
        });
      return function cleanup() {
        cancelled = true;
      };
    },
    [chatFilter],
  );

  function loadMore() {
    // The button renders only while `cursor` is non-null and is `disabled`
    // while a page is in flight, so re-entrancy can't happen.
    const epoch = epochRef.current;
    setLoadingMore(true);
    listMemoryRecords({ limit: PAGE_LIMIT, cursor, chatId: chatFilter })
      .then(function onMore(page) {
        if (!mountedRef.current || epochRef.current !== epoch) return;
        setGroups(function appendGroups(prev) {
          return prev.concat(page.groups);
        });
        setSummaries(function appendSummaries(prev) {
          return prev.concat(page.summaries);
        });
        setCursor(page.next_cursor);
      })
      .catch(function onMoreError() {
        /* transient — keep the list + the button so the user can retry */
      })
      .finally(function settle() {
        if (mountedRef.current && epochRef.current === epoch) setLoadingMore(false);
      });
  }

  function clearChatFilter() {
    const next = new URLSearchParams(searchParams);
    next.delete(CHAT_PARAM);
    setSearchParams(next, { replace: true });
  }

  function matchesKind(record) {
    return kindFilter === "all" || record.kind === kindFilter;
  }

  // The kind filter narrows records within a chat; a chat left with no
  // matching records drops out of the list entirely rather than rendering
  // as a bare heading. Summaries have no `kind` and are unaffected.
  const visibleGroups = groups
    .map(function filterGroup(group) {
      return { ...group, records: group.records.filter(matchesKind) };
    })
    .filter(function nonEmptyGroup(group) {
      return group.records.length > 0;
    });

  const storesNothing = groups.length === 0 && summaries.length === 0;

  return (
    <div className="set-group">
      <h3>What Channel remembers</h3>

      {status === "loading" && <p className="mem-note">Loading…</p>}

      {status === "error" && (
        <p className="mem-note" role="alert">
          Couldn&apos;t load what Channel remembers. Reload to try again.
        </p>
      )}

      {status === "ready" && (
        <>
          <p className="mem-lead">{recallSentence(recall)}</p>

          {withheld > 0 && (
            <p className="mem-note" role="status">
              At least {withheld} stored {plural(withheld, "record")} in your memory
              store couldn&apos;t be matched to one of your chats — not shown here.
            </p>
          )}

          <div className="mem-filters">
            {chatFilter && (
              <button type="button" className="mem-chatfilter" onClick={clearChatFilter}>
                <Icon name="close" size={13} /> Showing one chat — show all
              </button>
            )}
            <div className="seg-ctl" role="group" aria-label="Filter records by kind">
              {KIND_FILTERS.map(([value, label]) => (
                <KindFilterButton
                  key={value}
                  value={value}
                  label={label}
                  active={kindFilter === value}
                  onPick={setKindFilter}
                />
              ))}
            </div>
          </div>

          {storesNothing && (
            <p className="mem-empty">
              Nothing stored yet — that&apos;s expected on a new account. As you chat,
              everything Channel keeps will be listed here.
            </p>
          )}

          {!storesNothing && visibleGroups.length === 0 && (
            <p className="mem-empty">No stored records of this kind.</p>
          )}

          {visibleGroups.map((group) => (
            <MemoryGroup key={group.chat_id} group={group} />
          ))}

          {summaries.length > 0 && <SummaryGroup summaries={summaries} />}

          {cursor && (
            <button
              type="button"
              className="mem-loadmore"
              onClick={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? "Loading…" : "Load more"}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function KindFilterButton({ value, label, active, onPick }) {
  function pick() {
    onPick(value);
  }
  return (
    <button type="button" className={active ? "on" : ""} onClick={pick} aria-pressed={active}>
      {label}
    </button>
  );
}

function MemoryGroup({ group }) {
  return (
    <section className="mem-group" aria-label={chatLabel(group)}>
      <h4 className="mem-group-head">
        <Icon name="chat" size={14} /> {chatLabel(group)}
      </h4>
      {group.records.map((record) => (
        <MemoryRecordRow key={record.record_id} record={record} />
      ))}
      {group.records_truncated && (
        <p className="mem-note">
          This chat holds more records than one page shows — its oldest aren&apos;t listed.
        </p>
      )}
    </section>
  );
}

function MemoryRecordRow({ record }) {
  return (
    <article className="mem-record">
      <div className="mem-record-meta">
        <span className="mem-kind">{kindLabel(record.kind)}</span>
        <span className="mem-role">{roleLabel(record.role)}</span>
        <span className="mem-time">{relativeTime(record.created_at)}</span>
        {record.used_in_recall && (
          <span className="mem-badge">
            <Icon name="check" size={12} /> Used in recall
          </span>
        )}
      </div>
      {/*
        PLAIN TEXT, DELIBERATELY — this must never go through
        `renderMarkdown.jsx`. Stored memory is attacker-influenced text, and
        #465 tracks that exact forged-Markdown surface on the system-prompt
        side; rendering it as Markdown here would re-introduce the forgery
        surface in the UI. Plain text is also what an audit view wants: the
        bytes, not a rendering of them. Epic #129 decision 11.
      */}
      <p className="mem-text">{record.text}</p>
    </article>
  );
}

function SummaryGroup({ summaries }) {
  return (
    <section className="mem-group" aria-label="Chat summaries">
      <h4 className="mem-group-head">
        <Icon name="doc" size={14} /> Chat summaries
      </h4>
      <p className="mem-note">
        Read-only. Channel writes a rolling summary of each long chat and injects it
        on every turn of that chat. It is generated from the chat, so it can&apos;t be
        edited — and it is removed when the chat is deleted.
      </p>
      {summaries.map((summary) => (
        <article className="mem-record" key={summary.chat_id}>
          <div className="mem-record-meta">
            <span className="mem-kind">Summary</span>
            <span className="mem-role">{summary.chat_title || summary.chat_id}</span>
            <span className="mem-time">{relativeTime(summary.updated_at)}</span>
          </div>
          {/* Plain text for the same reason as MemoryRecordRow — see above. */}
          <p className="mem-text">{summary.text}</p>
        </article>
      ))}
    </section>
  );
}
